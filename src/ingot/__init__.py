"""Runtime helpers for kiln-generated FastAPI projects.

The submodules group related primitives -- ``ingot.auth``,
``ingot.documents``, ``ingot.filters``, ``ingot.ordering``,
``ingot.pagination``, ``ingot.responses``.  Generated code imports
from the submodule that owns the symbol
(``from ingot.documents import bind_document_model``,
``from ingot.auth import session_auth``).

The historical flat re-exports at the package root (``from ingot
import apply_filters, ...``) are retained for legacy callers but
new code should reach into the submodule directly.  The
documents-related symbols are *not* re-exported -- they were added
after the namespacing convention so they live under
:mod:`ingot.documents` only.

Everything here is pure Python -- the kiln CLI knows to emit
imports pointing at this package instead of scaffolding a
``utils.py`` into the generated app.
"""

from __future__ import annotations

from ingot.auth import (
    clear_session,
    decode_jwt,
    encode_jwt,
    issue_session,
    session_auth,
)
from ingot.filters import FilterOp, apply_filters
from ingot.ordering import SortDirection, apply_ordering
from ingot.pagination import apply_keyset_pagination, apply_offset_pagination
from ingot.responses import assert_rowcount, get_object_from_query_or_404

__all__ = [
    "FilterOp",
    "SortDirection",
    "apply_filters",
    "apply_keyset_pagination",
    "apply_offset_pagination",
    "apply_ordering",
    "assert_rowcount",
    "clear_session",
    "decode_jwt",
    "encode_jwt",
    "get_object_from_query_or_404",
    "issue_session",
    "session_auth",
]
