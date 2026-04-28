"""Action availability for kiln-generated FastAPI projects.

An *action* is anything you can do to (or with) a resource: the
built-in CRUD ops (``get``, ``list``, ``create``, ``update``,
``delete``) plus any custom action endpoints declared in the
spec.  Every action carries a single *guard* callable --

    async def can_<name>(resource, session) -> bool

-- that decides two things at once: whether the current session
may execute the action, and whether the action should appear in
serialized responses so the frontend can show the corresponding
button.  Object-scope guards see the resource instance; collection-
scope guards see ``None`` (there is no per-row resource yet).

Generated code emits one ``actions.py`` per app holding tuples of
:class:`ActionSpec` per resource (object and collection scopes
kept separate).  The route-handler templates call
:func:`available_actions` against the right tuple to populate the
``actions`` field on response payloads, and call the matching
``can`` callable directly before executing each handler so the
visibility predicate and the authorization gate can never drift.
"""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

Scope = Literal["object", "collection"]
"""Whether an action targets a single resource or a collection.

Object-scope actions take ``(resource, session)``; collection-
scope actions take ``(None, session)``.  The frontend uses this
to decide where to render the button -- per row or once on the
list page.
"""

CanCallable = Callable[[Any, Any], Awaitable[bool]]
"""Signature every guard conforms to: ``(resource, session) -> bool``.

``resource`` is the SQLAlchemy instance for object-scope actions
or ``None`` for collection-scope actions; ``session`` is whatever
:func:`ingot.auth.session_auth` resolves -- the type is left open
because the consumer picks the session model.
"""


class ActionRef(BaseModel):
    """One action exposed in a serialized response.

    Carries the bare minimum the frontend needs to render a
    button: the action name (matches the operation name on the
    backend) and its scope.  Kept Pydantic so the OpenAPI schema
    surfaces a stable shape; consumers downstream get a typed TS
    interface for free.

    Attributes:
        name: Operation name (e.g. ``"publish"``, ``"update"``).
        scope: ``"object"`` for per-row actions, ``"collection"``
            for actions that target the resource as a whole.

    """

    name: str
    scope: Scope


@dataclass(frozen=True)
class ActionSpec:
    """Generator-emitted descriptor for one action.

    Lives in the per-app ``actions.py`` registry.  The route
    handlers and serializers consume :class:`ActionSpec` tuples
    via :func:`available_actions`; nothing outside generated code
    should construct these by hand.

    Attributes:
        name: Operation name.
        can: Async guard returning ``True`` when the action is
            available.  Bound to :func:`always_true` when the
            spec did not declare a ``can`` dotted path.
        is_object_action: ``True`` for object-scope actions,
            ``False`` for collection-scope actions.  Drives the
            :class:`ActionRef` ``scope`` field and disambiguates
            which tuple a spec belongs in.

    """

    name: str
    can: CanCallable
    is_object_action: bool

    @property
    def scope(self) -> Scope:
        """Match :class:`ActionRef.scope` for this spec."""
        return "object" if self.is_object_action else "collection"


async def always_true(_resource: Any, _session: Any) -> bool:
    """Default guard used when an action declares no ``can`` path.

    Returning ``True`` unconditionally matches the historical
    behavior of generated handlers (auth handled at the route
    level, no per-action gating); opting in to action gating is
    additive.
    """
    return True


async def available_actions(
    resource: Any,
    session: Any,
    specs: Iterable[ActionSpec],
) -> list[ActionRef]:
    """Return the subset of *specs* whose guards pass for *session*.

    The guard for each spec is awaited in declaration order; specs
    whose guard returns ``False`` are dropped.  Order is preserved
    so the frontend can rely on a stable button layout driven by
    the spec.

    Args:
        resource: The SQLAlchemy instance for object-scope dumps,
            or ``None`` for collection-scope dumps.
        session: Whatever the auth dep resolved -- passed through
            to each guard untouched.
        specs: Iterable of :class:`ActionSpec`; typically a tuple
            literal from the generated per-app ``actions.py``.

    Returns:
        List of :class:`ActionRef`, one per spec whose guard
        returned ``True``, in spec order.

    """
    return [
        ActionRef(name=spec.name, scope=spec.scope)
        for spec in specs
        if await spec.can(resource, session)
    ]


async def filter_visible(
    rows: Iterable[Any],
    session: Any,
    can: CanCallable,
) -> list[Any]:
    """Drop rows the *can_list*-style guard rejects for *session*.

    Generated list handlers call this between the DB fetch and
    serialization: each row is run through the row-level
    visibility guard, and only survivors reach the response.
    Pagination math therefore reflects the user's view, not the
    raw row count -- callers that need exact counts must scope
    the underlying query.

    Guards run sequentially by design: visibility predicates
    should be pure checks against in-memory state, not per-row
    I/O.  If a consumer needs a DB lookup for visibility, the
    right fix is to scope the query (or eager-load the field)
    rather than parallelize the guard.

    Args:
        rows: Iterable of SQLAlchemy instances from the list
            query.
        session: Auth dep result; forwarded to the guard.
        can: The row-level guard, typically ``can_list_<resource>``
            from the consumer's module.

    Returns:
        List of rows whose guard returned ``True``, in input
        order.

    """
    return [row for row in rows if await can(row, session)]
