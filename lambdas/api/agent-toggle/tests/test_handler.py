"""Tests for agent-toggle pure update building."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "agent_toggle_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

build_update = _mod.build_update


def test_build_update_disable() -> None:
    upd = build_update(False, "sub-123")
    assert upd["ExpressionAttributeValues"][":e"] is False
    assert upd["ExpressionAttributeValues"][":u"] == "sub-123"
    assert "enabled = :e" in upd["UpdateExpression"]


def test_authz_forbidden_when_group_empty() -> None:
    """audit B3: a JWT with an empty cognito:groups claim must get 403."""
    event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"cognito:groups": "[]"}}}},
        "body": "{}",
    }
    result = _mod.handler(event, None)
    assert result["statusCode"] == 403


def test_authz_forbidden_when_group_missing() -> None:
    """audit B3: a JWT with no cognito:groups claim must get 403."""
    event = {"requestContext": {"authorizer": {"jwt": {"claims": {}}}}, "body": "{}"}
    result = _mod.handler(event, None)
    assert result["statusCode"] == 403
