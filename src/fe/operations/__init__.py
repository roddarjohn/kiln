"""fe operations.

Each module under :mod:`fe.operations` defines one foundry
operation; all are registered as entry points under the
``fe.operations`` group in pyproject.toml.

Re-exports the operation classes here so users (and tests) can
``from fe.operations import OpenApiTsConfig`` without knowing
which submodule each lives in.
"""

from fe.operations.auth import Auth
from fe.operations.openapi_ts import OpenApiTsConfig
from fe.operations.resource_action import ResourceAction
from fe.operations.resource_form import ResourceForm
from fe.operations.resource_list import ResourceList
from fe.operations.scaffold import Scaffold

__all__ = [
    "Auth",
    "OpenApiTsConfig",
    "ResourceAction",
    "ResourceForm",
    "ResourceList",
    "Scaffold",
]
