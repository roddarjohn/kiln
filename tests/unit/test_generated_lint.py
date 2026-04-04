"""Verify that generated files pass ruff lint and format checks.

If these tests fail, the Jinja2 templates are producing code
that a downstream consumer's ruff setup would flag as
unfixable.  Fix the templates, not the ruff config here.
"""

import subprocess
import textwrap
from pathlib import Path

import pytest

from kiln.config.schema import (
    AuthConfig,
    DatabaseConfig,
    KilnConfig,
    OperationConfig,
    ResourceConfig,
)
from kiln.generators.registry import GeneratorRegistry


def _ruff_toml() -> str:
    """Minimal ruff config matching the project's line-length."""
    return textwrap.dedent("""\
        line-length = 80

        [lint]
        select = [
            "E",    # pycodestyle errors
            "W",    # pycodestyle warnings
            "F",    # pyflakes
            "I",    # isort
            "UP",   # pyupgrade
        ]
    """)


# -------------------------------------------------------------------
# Configs that exercise all major template paths
# -------------------------------------------------------------------

FULL_CRUD = KilnConfig(
    module="myapp",
    auth=AuthConfig(
        verify_credentials_fn="myapp.auth.verify",
    ),
    resources=[
        ResourceConfig(
            model="myapp.models.User",
            pk="id",
            pk_type="uuid",
            require_auth=False,
            operations=[
                "get",
                OperationConfig(
                    name="list",
                    fields=[
                        {"name": "id", "type": "uuid"},
                        {"name": "email", "type": "email"},
                    ],
                ),
                OperationConfig(
                    name="create",
                    require_auth=True,
                    fields=[
                        {"name": "email", "type": "email"},
                    ],
                ),
                OperationConfig(
                    name="update",
                    require_auth=True,
                    fields=[
                        {"name": "email", "type": "email"},
                    ],
                ),
                OperationConfig(name="delete", require_auth=True),
            ],
        ),
    ],
)

LIST_WITH_EXTENSIONS = KilnConfig(
    module="catalog",
    resources=[
        ResourceConfig(
            model="catalog.models.Product",
            pk="id",
            pk_type="uuid",
            require_auth=False,
            operations=[
                "get",
                OperationConfig(
                    name="list",
                    fields=[
                        {"name": "id", "type": "uuid"},
                        {"name": "name", "type": "str"},
                        {"name": "price", "type": "float"},
                        {"name": "active", "type": "bool"},
                    ],
                    filters={"fields": ["name", "price", "active"]},
                    ordering={
                        "fields": ["name", "price"],
                        "default": "name",
                        "default_dir": "asc",
                    },
                    pagination={
                        "mode": "offset",
                        "default_page_size": 20,
                        "max_page_size": 50,
                    },
                ),
            ],
        ),
    ],
)

KEYSET_PAGINATION = KilnConfig(
    module="store",
    resources=[
        ResourceConfig(
            model="store.models.Item",
            pk="id",
            pk_type="uuid",
            require_auth=False,
            operations=[
                OperationConfig(
                    name="list",
                    fields=[
                        {"name": "id", "type": "uuid"},
                        {"name": "sku", "type": "str"},
                    ],
                    pagination={
                        "mode": "keyset",
                        "cursor_field": "id",
                        "cursor_type": "uuid",
                        "default_page_size": 25,
                        "max_page_size": 100,
                    },
                ),
            ],
        ),
    ],
)

INT_PK_NO_AUTH = KilnConfig(
    module="tags",
    resources=[
        ResourceConfig(
            model="tags.models.Tag",
            pk="id",
            pk_type="int",
            require_auth=False,
            operations=[
                "get",
                "list",
                OperationConfig(
                    name="create",
                    fields=[{"name": "name", "type": "str"}],
                ),
                OperationConfig(
                    name="update",
                    fields=[{"name": "name", "type": "str"}],
                ),
                "delete",
            ],
        ),
    ],
)

MULTI_DB = KilnConfig(
    module="analytics",
    databases=[
        DatabaseConfig(key="primary", default=True),
        DatabaseConfig(
            key="analytics",
            url_env="ANALYTICS_DATABASE_URL",
            pool_size=2,
        ),
    ],
    resources=[
        ResourceConfig(
            model="analytics.models.Event",
            pk="id",
            pk_type="uuid",
            db_key="analytics",
            require_auth=False,
            operations=[
                OperationConfig(
                    name="create",
                    fields=[
                        {"name": "event_type", "type": "str"},
                        {"name": "payload", "type": "json"},
                        {"name": "occurred_at", "type": "datetime"},
                    ],
                ),
            ],
        ),
    ],
)

CREATE_ONLY = KilnConfig(
    module="logs",
    auth=AuthConfig(
        verify_credentials_fn="myapp.auth.verify",
    ),
    resources=[
        ResourceConfig(
            model="logs.models.AuditEntry",
            pk="id",
            pk_type="uuid",
            require_auth=True,
            operations=[
                OperationConfig(
                    name="create",
                    fields=[
                        {"name": "actor_email", "type": "email"},
                        {"name": "action", "type": "str"},
                        {"name": "created_at", "type": "date"},
                    ],
                ),
            ],
        ),
    ],
)


CONFIGS = {
    "full_crud": FULL_CRUD,
    "list_extensions": LIST_WITH_EXTENSIONS,
    "keyset_pagination": KEYSET_PAGINATION,
    "int_pk_no_auth": INT_PK_NO_AUTH,
    "multi_db": MULTI_DB,
    "create_only": CREATE_ONLY,
}


def _write_generated(
    cfg: KilnConfig,
    tmp_path: Path,
) -> Path:
    """Generate files and write them under *tmp_path*."""
    out = tmp_path / "generated"
    out.mkdir()

    # Write ruff config
    (tmp_path / "ruff.toml").write_text(_ruff_toml())

    files = GeneratorRegistry.default().run(cfg)
    for f in files:
        dest = out / f.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f.content)
    return out


def _ruff(tmp_path, *args):
    """Run ruff via uv with the generated ruff config."""
    return subprocess.run(
        [
            "uv",
            "run",
            "--group",
            "lint",
            "ruff",
            *args,
            "--config",
            str(tmp_path / "ruff.toml"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("name", CONFIGS)
def test_generated_no_unfixable_lint(name, tmp_path):
    out = _write_generated(CONFIGS[name], tmp_path)

    # Auto-fix what ruff can fix.
    _ruff(tmp_path, "check", "--fix", str(out))
    _ruff(tmp_path, "format", str(out))

    # Verify nothing unfixable remains.
    check = _ruff(tmp_path, "check", str(out))
    assert check.returncode == 0, (
        f"unfixable lint issues for {name}:\n{check.stdout}\n{check.stderr}"
    )

    fmt = _ruff(tmp_path, "format", "--check", str(out))
    assert fmt.returncode == 0, (
        f"unfixable format issues for {name}:\n{fmt.stdout}\n{fmt.stderr}"
    )
