"""Tests for kiln.operations._introspect."""

from __future__ import annotations

import pytest

from kiln.operations._introspect import introspect_action_fn

_STUB = "tests.unit._action_stubs"


def test_introspect_object_action_with_body():
    info = introspect_action_fn(
        f"{_STUB}.object_action_with_body",
        f"{_STUB}.StubModel",
    )
    assert info.is_object_action is True
    assert info.model_param_name == "obj"
    assert info.request_class == "StubRequest"
    assert info.request_module == _STUB
    assert info.response_class == "StubResponse"
    assert info.response_module == _STUB


def test_introspect_object_action_no_body():
    info = introspect_action_fn(
        f"{_STUB}.object_action_no_body",
        f"{_STUB}.StubModel",
    )
    assert info.is_object_action is True
    assert info.model_param_name == "obj"
    assert info.request_class is None
    assert info.response_class == "StubResponse"


def test_introspect_collection_action():
    info = introspect_action_fn(
        f"{_STUB}.collection_action_with_body",
        f"{_STUB}.StubModel",
    )
    assert info.is_object_action is False
    assert info.model_param_name is None
    assert info.request_class == "StubRequest"
    assert info.response_class == "StubResponse"


def test_introspect_collection_no_body():
    info = introspect_action_fn(
        f"{_STUB}.collection_action_no_body",
        f"{_STUB}.StubModel",
    )
    assert info.is_object_action is False
    assert info.request_class is None
    assert info.response_class == "StubResponse"


def test_introspect_non_basemodel_return_raises():
    with pytest.raises(TypeError, match="BaseModel"):
        introspect_action_fn(
            f"{_STUB}.action_no_return",
            f"{_STUB}.StubModel",
        )


def test_introspect_no_annotations_raises():
    with pytest.raises(TypeError, match="BaseModel"):
        introspect_action_fn(
            f"{_STUB}.action_no_annotations",
            f"{_STUB}.StubModel",
        )


def test_introspect_bad_module_raises():
    with pytest.raises(ValueError, match="Cannot import"):
        introspect_action_fn(
            "nonexistent.module.fn",
            f"{_STUB}.StubModel",
        )


def test_introspect_missing_attr_raises():
    with pytest.raises(ValueError, match="not found"):
        introspect_action_fn(
            f"{_STUB}.does_not_exist",
            f"{_STUB}.StubModel",
        )


def test_introspect_invalid_dotted_path_raises():
    with pytest.raises(ValueError, match="valid dotted path"):
        introspect_action_fn("nomodule", f"{_STUB}.StubModel")


def test_introspect_multiple_basemodel_params_raises():
    with pytest.raises(ValueError, match="multiple"):
        introspect_action_fn(
            f"{_STUB}.action_two_bodies",
            f"{_STUB}.StubModel",
        )
