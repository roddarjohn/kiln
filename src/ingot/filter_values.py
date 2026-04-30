"""Body schema for the project-wide value-provider endpoint.

Carries the autocomplete query and an optional limit.  The
endpoint is intentionally single-page (no cursor): autocomplete
UX narrows by typing more characters, not by paginating.
"""

from pydantic import BaseModel

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


class FilterValuesRequest(BaseModel):
    """Common search params used by every value-provider runner."""

    q: str | None = None
    limit: int | None = None


def resolved_limit(req_limit: int | None) -> int:
    """Clamp *req_limit* to ``[1, _MAX_LIMIT]`` with a sensible default."""
    if req_limit is None:
        return _DEFAULT_LIMIT

    return max(1, min(req_limit, _MAX_LIMIT))
