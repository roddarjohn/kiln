import ast

import pytest

from kiln.config.schema import (
    AuthConfig,
    KilnConfig,
    OperationConfig,
    ResourceConfig,
)
from kiln.generators.fastapi.resource import ResourceGenerator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_resource() -> ResourceConfig:
    """Resource with generate_tests enabled and full CRUD."""
    return ResourceConfig(
        model="myapp.models.User",
        pk="id",
        pk_type="uuid",
        require_auth=False,
        generate_tests=True,
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
    )


@pytest.fixture
def test_config(test_resource) -> KilnConfig:
    return KilnConfig(
        module="myapp",
        auth=AuthConfig(),
        resources=[test_resource],
    )


@pytest.fixture
def no_auth_config(test_resource) -> KilnConfig:
    return KilnConfig(
        module="myapp",
        resources=[test_resource],
    )


@pytest.fixture
def no_test_resource() -> ResourceConfig:
    """Resource without generate_tests (default)."""
    return ResourceConfig(
        model="myapp.models.User",
        pk="id",
        pk_type="uuid",
        require_auth=False,
        operations=["get", "list"],
    )


@pytest.fixture
def no_test_config(no_test_resource) -> KilnConfig:
    return KilnConfig(
        module="myapp",
        resources=[no_test_resource],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_test_file(config: KilnConfig) -> str | None:
    """Generate and return the test file content, or None."""
    files = ResourceGenerator().generate(config)
    for f in files:
        if "tests/test_" in f.path:
            return f.content
    return None


def _get_test_path(config: KilnConfig) -> str | None:
    """Generate and return the test file path, or None."""
    files = ResourceGenerator().generate(config)
    for f in files:
        if "tests/test_" in f.path:
            return f.path
    return None


# ---------------------------------------------------------------------------
# No test file when disabled
# ---------------------------------------------------------------------------


def test_no_test_file_when_disabled(no_test_config):
    """Test file is not generated when generate_tests is False."""
    assert _get_test_file(no_test_config) is None


# ---------------------------------------------------------------------------
# Test file path
# ---------------------------------------------------------------------------


def test_test_file_path(test_config):
    """Test file is at the expected path."""
    path = _get_test_path(test_config)
    assert path == "myapp/tests/test_user.py"


# ---------------------------------------------------------------------------
# Valid Python
# ---------------------------------------------------------------------------


def test_test_file_valid_python(test_config):
    """Generated test file must be syntactically valid Python."""
    content = _get_test_file(test_config)
    assert content is not None
    ast.parse(content)


# ---------------------------------------------------------------------------
# Contains expected test functions
# ---------------------------------------------------------------------------


def test_contains_get_tests(test_config):
    content = _get_test_file(test_config)
    assert "async def test_get_success" in content
    assert "async def test_get_not_found" in content


def test_contains_list_tests(test_config):
    content = _get_test_file(test_config)
    assert "async def test_list_success" in content


def test_contains_create_tests(test_config):
    content = _get_test_file(test_config)
    assert "async def test_create_success" in content
    assert "async def test_create_invalid_body" in content


def test_contains_update_tests(test_config):
    content = _get_test_file(test_config)
    assert "async def test_update_success" in content
    assert "async def test_update_not_found" in content


def test_contains_delete_tests(test_config):
    content = _get_test_file(test_config)
    assert "async def test_delete_success" in content
    assert "async def test_delete_not_found" in content


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


def test_contains_auth_tests_when_auth_configured(test_config):
    """Operations with require_auth=True should have unauthorized tests."""
    content = _get_test_file(test_config)
    # update and delete have require_auth=True
    assert "async def test_update_unauthorized" in content
    assert "async def test_delete_unauthorized" in content


def test_no_auth_tests_for_unrestricted_ops(test_config):
    """Operations without auth should not have unauthorized tests."""
    content = _get_test_file(test_config)
    # get and list have require_auth=False (resource default)
    assert "async def test_get_unauthorized" not in content
    assert "async def test_list_unauthorized" not in content


def test_no_auth_tests_when_no_auth(no_auth_config):
    """No unauthorized tests when auth is not configured at all."""
    content = _get_test_file(no_auth_config)
    assert content is not None
    assert "unauthorized" not in content


# ---------------------------------------------------------------------------
# Serializer tests
# ---------------------------------------------------------------------------


def test_contains_serializer_test(test_config):
    """Serializer test present when resource schema has fields."""
    content = _get_test_file(test_config)
    assert "def test_to_user_resource_maps_fields" in content


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def test_contains_fixtures(test_config):
    content = _get_test_file(test_config)
    assert "def mock_db" in content
    assert "def app" in content
    assert "async def client" in content


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


def test_contains_imports(test_config):
    content = _get_test_file(test_config)
    assert "import pytest" in content
    assert "from unittest.mock import" in content
    assert "from httpx import" in content
    assert "from fastapi import FastAPI" in content
