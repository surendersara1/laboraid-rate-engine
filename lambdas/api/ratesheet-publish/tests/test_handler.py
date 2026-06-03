"""Tests for the publish guard (SOW contract: 409 unless approved)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location(
    "publish_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

publish_guard = _mod.publish_guard
handler = _mod.handler


def test_publish_blocked_unless_approved() -> None:
    for state in ("pending_review", "rejected", "published"):
        status, body = publish_guard(state)
        assert status == 409
        assert body["error"] == "not_approved"


def test_publish_allowed_when_approved() -> None:
    status, body = publish_guard("approved")
    assert status == 200
    assert body["approval_state"] == "published"


def _event(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "pathParameters": {"local": "150", "period": "2025-07-01"},
        "body": json.dumps(body),
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "u-1"}}}},
    }


def test_publish_ignores_client_supplied_state(monkeypatch: Any) -> None:
    """SOW anti-bypass (audit B1): a client POSTing approval_state=approved must
    NOT publish when Aurora says the period is still pending_review."""
    monkeypatch.setattr(_mod, "read_approval_state", lambda local, period: "pending_review")
    result = handler(_event({"approval_state": "approved"}), None)
    assert result["statusCode"] == 409
    assert json.loads(result["body"])["error"] == "not_approved"


def test_publish_allowed_when_aurora_approved(monkeypatch: Any) -> None:
    monkeypatch.setattr(_mod, "read_approval_state", lambda local, period: "approved")
    result = handler(_event({}), None)
    assert result["statusCode"] == 200
    assert json.loads(result["body"])["published_by"] == "u-1"


def test_publish_404_when_period_missing(monkeypatch: Any) -> None:
    monkeypatch.setattr(_mod, "read_approval_state", lambda local, period: None)
    result = handler(_event({"approval_state": "approved"}), None)
    assert result["statusCode"] == 404
