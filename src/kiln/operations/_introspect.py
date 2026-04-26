"""Action-function introspection.

Used by the :class:`~kiln.operations.action.Action` operation to
inspect a consumer-supplied callable and decide what code to
generate.  The function's parameters classify the action as either
*object* (operates on one model instance) or *collection* (operates
on the whole table), and the return type drives the response
schema.
"""

from __future__ import annotations

import importlib
import inspect
import typing
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

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
    sig = inspect.signature(fn)

    model_param_name: str | None = None
    model_class_param_name: str | None = None
    request_class: str | None = None
    request_module: str | None = None

    for param_name in sig.parameters:
        hint = hints.get(param_name)

        if hint is None or _is_async_session(hint):
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
    module_path, _, attr = dotted.rpartition(".")
    if not module_path:
        msg = f"'{dotted}' is not a valid dotted path."
        raise ValueError(msg)

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
    fn_dotted: str,
) -> dict[str, type]:
    """Resolve type hints for a callable, wrapping failures."""
    try:
        return typing.get_type_hints(fn)

    except Exception as exc:
        msg = f"Cannot resolve type annotations for '{fn_dotted}': {exc}"
        raise ValueError(msg) from exc


def _matches_model_class_param(hint: object, model_cls: object) -> bool:
    """Return ``True`` when *hint* is ``type[X]`` and *model_cls* is X or a sub.

    Counterpart to :func:`_matches_model` for the *class* (not the
    instance) of the resource model.  Lets a generic collection
    action declare ``model_cls: type[DocumentMixin]`` and have the
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
    annotated ``DocumentMixin`` matches any concrete model that
    extends ``DocumentMixin``, so :mod:`ingot.documents` can ship
    reusable upload/download actions.

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


def _is_async_session(hint: object) -> bool:
    """Return ``True`` when *hint* is SQLAlchemy's ``AsyncSession``.

    Compared by class name to avoid importing SQLAlchemy from this
    module; consumer code may have its own ``AsyncSession`` alias
    that we still want to skip.
    """
    return isinstance(hint, type) and hint.__name__ == "AsyncSession"


def _is_pydantic_model(hint: object) -> bool:
    """Return ``True`` when *hint* is a ``BaseModel`` subclass."""
    return isinstance(hint, type) and issubclass(hint, BaseModel)


def _validate_return_type(
    hints: dict[str, type],
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

    """
    return_hint = hints.get("return")
    if return_hint is None:
        msg = (
            f"Action '{fn_dotted}' has no return type annotation. "
            f"Annotate the return as a BaseModel subclass or 'None'."
        )
        raise TypeError(msg)

    # ``-> None`` resolves to the NoneType class via get_type_hints.
    if return_hint is type(None):
        return None, None

    if not _is_pydantic_model(return_hint):
        msg = (
            f"Action '{fn_dotted}' return type '{return_hint}' is "
            f"not a BaseModel subclass or 'None'."
        )
        raise TypeError(msg)

    return return_hint.__name__, return_hint.__module__
