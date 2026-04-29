"""Action-function introspection.

Used by the :class:`~be.operations.action.Action` operation to
inspect a consumer-supplied callable and decide what code to
generate.  The function's parameters classify the action as either
*object* (operates on one model instance) or *collection* (operates
on the whole table), and the return type drives the response
schema.
"""

import annotationlib
import contextlib
import importlib
import inspect
import typing
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

from foundry.naming import Name

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class IntrospectedAction:
    """Result of inspecting a consumer's action function.

    Attributes:
        model_param_name: Name of the parameter holding the model
            instance, or ``None`` if the function doesn't take one
            (collection action).
        model_class_param_name: Name of the parameter typed
            ``type[X]`` where the resource model is a subclass of
            X.  The generated handler passes the resource's mapped
            class as that kwarg, so a collection action can INSERT
            without a per-resource factory binding.  ``None`` when
            no such parameter exists.
        request_class: Name of the Pydantic request-body class, if
            any.
        request_module: Module containing ``request_class``.
        response_class: Name of the Pydantic response class, or
            ``None`` when the function returns ``-> None`` (the
            generated route emits 204 No Content with no body).
        response_module: Module containing ``response_class``, or
            ``None`` when the function returns ``-> None``.

    """

    model_param_name: str | None
    model_class_param_name: str | None
    request_class: str | None
    request_module: str | None
    response_class: str | None
    response_module: str | None

    @property
    def is_object_action(self) -> bool:
        """``True`` when the action takes a model instance.

        Object actions map to ``/{pk}/{slug}`` paths and fetch the
        row by primary key; collection actions map to ``/{slug}``
        and operate on the whole table.
        """
        return bool(self.model_param_name)

    @property
    def returns_none(self) -> bool:
        """``True`` when the function is annotated ``-> None``.

        The generated route emits 204 No Content with no body in
        this case; the action template skips ``return result``.
        """
        return self.response_class is None


def introspect_action_fn(
    fn_dotted: str,
    model_class_path: str,
) -> IntrospectedAction:
    """Import an action function and inspect its annotations.

    Args:
        fn_dotted: Dotted import path to the function,
            e.g. ``"blog.actions.publish_article"``.
        model_class_path: Dotted import path to the resource
            model, e.g. ``"blog.models.Article"``.

    Returns:
        An :class:`IntrospectedAction` with classified params.

    Raises:
        ValueError: If the function cannot be imported or has
            multiple body params.
        TypeError: If the return annotation is missing or is not a
            :class:`pydantic.BaseModel` subclass.

    """
    fn = _import_callable(fn_dotted)
    model_cls = _import_callable(model_class_path)
    hints = _resolve_hints(fn, fn_dotted)
    # FORWARDREF format keeps inspect.signature from evaluating
    # annotations -- _resolve_hints already did that.  Without this,
    # any unresolvable annotation would raise NameError here, before
    # the introspector got a chance to give a targeted diagnostic.
    sig = inspect.signature(
        fn, annotation_format=annotationlib.Format.FORWARDREF
    )

    model_param_name: str | None = None
    model_class_param_name: str | None = None
    request_class: str | None = None
    request_module: str | None = None

    for param_name in sig.parameters:
        hint = hints.get(param_name)

        if hint is None:
            continue

        # Unresolved ``ForwardRef`` -- typically a TYPE_CHECKING-guarded
        # infra import (``AsyncSession``, ``Logger``, etc).  We can't
        # classify it, so we treat it as a non-classifiable param and
        # skip.  The realistic risk -- a consumer guarding their own
        # model class or request body BaseModel -- is self-correcting:
        # generated code would be obviously wrong on first run.
        if isinstance(hint, annotationlib.ForwardRef):
            continue

        if _matches_model(hint, model_cls):
            model_param_name = param_name
            continue

        if _matches_model_class_param(hint, model_cls):
            model_class_param_name = param_name
            continue

        if not _is_pydantic_model(hint):
            continue

        if request_class is not None:
            msg = (
                f"Action '{fn_dotted}' has multiple BaseModel "
                f"parameters. Only one request body is allowed."
            )
            raise ValueError(msg)

        request_class = hint.__name__
        request_module = hint.__module__

    response_class, response_module = _validate_return_type(hints, fn_dotted)

    return IntrospectedAction(
        model_param_name=model_param_name,
        model_class_param_name=model_class_param_name,
        request_class=request_class,
        request_module=request_module,
        response_class=response_class,
        response_module=response_module,
    )


def _import_callable(dotted: str) -> Callable[..., object]:
    """Import a named attribute from a dotted path."""
    module_path, attr_name = Name.from_dotted(dotted)
    attr = attr_name.raw

    try:
        module = importlib.import_module(module_path)

    except ModuleNotFoundError as exc:
        msg = (
            f"Cannot import module '{module_path}' for '{dotted}'. "
            f"Ensure the consumer code is on sys.path."
        )
        raise ValueError(msg) from exc

    attribute = getattr(module, attr, None)

    if attribute is None:
        msg = f"'{attr}' not found in module '{module_path}'."
        raise ValueError(msg)

    return attribute


def _resolve_hints(
    fn: object,
    fn_dotted: str,  # noqa: ARG001
) -> dict[str, object]:
    """Resolve type hints, leaving unresolvable names as ``ForwardRef``.

    Uses :func:`annotationlib.get_annotations` with ``Format.FORWARDREF``
    so a single unresolvable annotation (typically a SQLAlchemy or
    other heavy import the consumer guarded with ``TYPE_CHECKING``)
    doesn't kill the whole resolution.  The classifier handles the
    common skippable case (``AsyncSession``) by name and raises a
    targeted error for any other unresolved name -- which is the
    diagnostic the consumer needs to fix their import.

    The string-bridge (``isinstance(ann, str)``) covers consumers
    that still use ``from __future__ import annotations``: under
    that import, all annotations come back as raw strings even from
    ``Format.FORWARDREF``, so we wrap them in :class:`ForwardRef`
    ourselves and then go through the same evaluate path.
    """
    raw = annotationlib.get_annotations(
        fn,
        format=annotationlib.Format.FORWARDREF,
    )
    fn_globals = getattr(fn, "__globals__", None)
    resolved: dict[str, object] = {}

    for name, ann in raw.items():
        value: object = ann

        if isinstance(value, str):
            value = annotationlib.ForwardRef(value, owner=fn)

        if isinstance(value, annotationlib.ForwardRef):
            with contextlib.suppress(NameError):
                value = value.evaluate(globals=fn_globals)

        resolved[name] = value

    return resolved


def _raise_unresolved(
    fn_dotted: str,
    param_name: str,
    hint: annotationlib.ForwardRef,
) -> typing.NoReturn:
    """Raise a targeted error for an unresolved parameter annotation."""
    msg = (
        f"Action '{fn_dotted}' parameter '{param_name}' has an "
        f"unresolved annotation '{hint.__forward_arg__}'. If this "
        f"name is imported under ``if TYPE_CHECKING:``, move the "
        f"import to module top-level so the introspector can see it."
    )
    raise ValueError(msg)


def _matches_model_class_param(hint: object, model_cls: object) -> bool:
    """Return ``True`` when *hint* is ``type[X]`` and *model_cls* is X or a sub.

    Counterpart to :func:`_matches_model` for the *class* (not the
    instance) of the resource model.  Lets a generic collection
    action declare ``model_cls: type[FileMixin]`` and have the
    handler plug in the concrete mapped class -- no per-resource
    factory binding required.

    The match goes through :func:`typing.get_origin`/``get_args`` so
    only true ``type[X]`` parameterizations qualify; a bare ``type``
    annotation doesn't, and neither does ``Type[X]`` from older
    pre-3.9 code paths because ``typing.Type`` resolves to ``type``
    under modern annotation evaluation.
    """
    origin = typing.get_origin(hint)

    if origin is not type:
        return False

    args = typing.get_args(hint)

    if len(args) != 1:
        return False

    return _matches_model(args[0], model_cls)


def _matches_model(hint: object, model_cls: object) -> bool:
    """Return ``True`` when *hint* is the model class or a supertype.

    The exact-identity case (``hint is model_cls``) is the common one
    -- a user-written action annotates its first parameter with the
    same SQLAlchemy class the resource points at.  The subclass case
    is what lets generic actions live in shared modules: a function
    annotated ``FileMixin`` matches any concrete model that extends
    ``FileMixin``, so :mod:`ingot.files` can ship reusable
    upload/download actions.

    ``object`` is rejected explicitly: any class is a subclass of
    ``object``, so without the guard a parameter typed ``object``
    would silently be treated as the model param.
    """
    return (
        isinstance(hint, type)
        and isinstance(model_cls, type)
        and hint is not object
        and issubclass(model_cls, hint)
    )


def _is_pydantic_model(hint: object) -> typing.TypeIs[type[BaseModel]]:
    """Return ``True`` when *hint* is a ``BaseModel`` subclass."""
    return isinstance(hint, type) and issubclass(hint, BaseModel)


def _validate_return_type(
    hints: dict[str, object],
    fn_dotted: str,
) -> tuple[str | None, str | None]:
    """Extract and validate the return type annotation.

    Returns:
        ``(class_name, module_path)`` tuple, or ``(None, None)``
        when the function is annotated ``-> None`` -- the action op
        treats that as "no body, 204 No Content".

    Raises:
        TypeError: If the return annotation is missing or is neither
            a ``BaseModel`` subclass nor ``None``.
        ValueError: If the return annotation is an unresolvable name
            (e.g. a ``TYPE_CHECKING``-guarded import).

    """
    if "return" not in hints:
        msg = (
            f"Action '{fn_dotted}' has no return type annotation. "
            f"Annotate the return as a BaseModel subclass or 'None'."
        )
        raise TypeError(msg)

    return_hint = hints["return"]

    if return_hint is None:
        return None, None

    if isinstance(return_hint, annotationlib.ForwardRef):
        _raise_unresolved(fn_dotted, "return", return_hint)

    if not _is_pydantic_model(return_hint):
        msg = (
            f"Action '{fn_dotted}' return type '{return_hint}' is "
            f"not a BaseModel subclass or 'None'."
        )
        raise TypeError(msg)

    return return_hint.__name__, return_hint.__module__
