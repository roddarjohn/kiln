"""Runtime helpers for kiln-generated FastAPI projects.

These are the shared route utilities that generated code imports
directly (``from ingot import apply_offset_pagination, ...``) rather
than having its own copy emitted into the target project.

Everything here is pure Python — the kiln CLI knows to emit imports
pointing at this module instead of scaffolding a ``utils.py`` into
the generated app.
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
from ingot.utils import run_once

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
    "run_once",
    "session_auth",
]
