"""Consumer-owned auth types for the playground.

The three dotted paths in ``examples/project.jsonnet`` -- ``auth.jwt({
credentials_schema, session_schema, validate_fn })`` -- resolve to
the symbols in :mod:`myapp.auth`.  Kiln owns the auth *package* (the
``get_session`` dep and the login/logout routes); this package owns
the three types that characterise the domain.
"""
