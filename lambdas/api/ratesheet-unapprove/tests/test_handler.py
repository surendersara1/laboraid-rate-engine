"""Tests for the unapprove transition + Aurora persistence + EventBridge emit."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location(
    "unapprove_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

unapprove_transition = _mod.unapprove_transition
handler = _mod.handler


def test_unapprove_only_original_approver() -> None:
    status, _ = unapprove_transition("approved", "user-b", "user-a")
    assert status == 403


def test_unapprove_ok_for_approver() -> None:
    status, body = unapprove_transition("approved", "user-a", "user-a")
    assert status == 200
    assert body["approval_state"] == "pending_review"


def test_unapprove_blocked_after_publish() -> None:
    status, _ = unapprove_transition("published", "user-a", "user-a")
    assert status == 409


def _event() -> dict[str, Any]:
    return {
        "pathParameters": {"local": "150", "period": "2025-07-01"},
        "body": json.dumps({"approval_state": "approved", "approved_by": "user-a"}),
        "requestContext": {
            "authorizer": {"jwt": {"claims": {"sub": "user-a", "cognito:groups": "[Business]"}}}
        },
    }


def test_unapprove_persists_and_emits(monkeypatch: Any) -> None:
    """audit B2: a successful unapprove must UPDATE Aurora AND PutEvents."""
    persisted: dict[str, Any] = {}
    emitted: dict[str, Any] = {}
    monkeypatch.setattr(
        _mod,
        "persist_unapproval",
        lambda local, period: persisted.update(local=local, period=period),
    )
    monkeypatch.setattr(
        _mod,
        "emit_event",
        lambda detail_type, detail: emitted.update(detail_type=detail_type, detail=detail),
    )
    result = handler(_event(), None)
    assert result["statusCode"] == 200
    assert persisted == {"local": "150", "period": "2025-07-01"}
    assert emitted["detail_type"] == "laboraid.rate-sheet.unapproved"


def test_unapprove_failure_neither_persists_nor_emits(monkeypatch: Any) -> None:
    calls: list[str] = []
    monkeypatch.setattr(_mod, "persist_unapproval", lambda *a: calls.append("persist"))
    monkeypatch.setattr(_mod, "emit_event", lambda *a: calls.append("emit"))
    event = _event()
    # requester (sub=user-b) is not the original approver -> 403, no writes.
    event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"] = "user-b"
    result = handler(event, None)
    assert result["statusCode"] == 403
    assert calls == []


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
