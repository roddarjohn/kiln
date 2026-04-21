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
        is_object_action: ``True`` when the function takes a model
            instance (path ``/{pk}/{slug}``); ``False`` for a
            collection action (path ``/{slug}``).
        model_param_name: Name of the parameter holding the model
            instance (object actions only).
        request_class: Name of the Pydantic request-body class, if
            any.
        request_module: Module containing ``request_class``.
        response_class: Name of the Pydantic response class.
        response_module: Module containing ``response_class``.

    """

    is_object_action: bool
    model_param_name: str | None
    request_class: str | None
    request_module: str | None
    response_class: str
    response_module: str


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

    is_object_action = False
    model_param_name: str | None = None
    request_class: str | None = None
    request_module: str | None = None

    for param_name in sig.parameters:
        hint = hints.get(param_name)
        if hint is None or _is_async_session(hint):
            continue
        if hint is model_cls:
            is_object_action = True
            model_param_name = param_name
        elif _is_pydantic_model(hint):
            if request_class is not None:
                msg = (
                    f"Action '{fn_dotted}' has multiple "
                    f"BaseModel parameters. Only one "
                    f"request body is allowed."
                )
                raise ValueError(msg)
            request_class = hint.__name__
            request_module = hint.__module__

    response_class, response_module = _validate_return_type(
        hints,
        fn_dotted,
    )

    return IntrospectedAction(
        is_object_action=is_object_action,
        model_param_name=model_param_name,
        request_class=request_class,
        request_module=request_module,
        response_class=response_class,
        response_module=response_module,
    )


def _import_callable(dotted: str) -> Callable[..., object]:
    """Import a name from a dotted path."""
    mod_path, _, attr = dotted.rpartition(".")
    if not mod_path:
        msg = f"'{dotted}' is not a valid dotted path."
        raise ValueError(msg)
    try:
        mod = importlib.import_module(mod_path)
    except ModuleNotFoundError as exc:
        msg = (
            f"Cannot import module '{mod_path}' for "
            f"'{dotted}'. Ensure the consumer code is "
            f"on sys.path."
        )
        raise ValueError(msg) from exc
    obj = getattr(mod, attr, None)
    if obj is None:
        msg = f"'{attr}' not found in module '{mod_path}'."
        raise ValueError(msg)
    return obj


def _resolve_hints(
    fn: object,
    fn_dotted: str,
) -> dict[str, type]:
    """Resolve type hints for a callable."""
    try:
        return typing.get_type_hints(fn)
    except Exception as exc:
        msg = f"Cannot resolve type annotations for '{fn_dotted}': {exc}"
        raise ValueError(msg) from exc


def _is_async_session(hint: object) -> bool:
    """Check whether *hint* is ``AsyncSession``."""
    return isinstance(hint, type) and hint.__name__ == "AsyncSession"


def _is_pydantic_model(hint: object) -> bool:
    """Check whether *hint* is a ``BaseModel`` subclass."""
    return isinstance(hint, type) and issubclass(hint, BaseModel)


def _validate_return_type(
    hints: dict[str, type],
    fn_dotted: str,
) -> tuple[str, str]:
    """Extract and validate the return type annotation.

    Returns:
        ``(class_name, module_path)`` tuple.

    Raises:
        TypeError: If the return annotation is missing or is not a
            ``BaseModel`` subclass.

    """
    return_hint = hints.get("return")
    if return_hint is None:
        msg = (
            f"Action '{fn_dotted}' has no return type "
            f"annotation. A BaseModel return type is "
            f"required."
        )
        raise TypeError(msg)
    if not _is_pydantic_model(return_hint):
        msg = (
            f"Action '{fn_dotted}' return type "
            f"'{return_hint}' is not a BaseModel "
            f"subclass. A BaseModel return type is "
            f"required."
        )
        raise TypeError(msg)
    return return_hint.__name__, return_hint.__module__
