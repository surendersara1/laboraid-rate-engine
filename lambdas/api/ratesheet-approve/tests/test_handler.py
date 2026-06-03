"""Tests for the approve transition + Aurora persistence + EventBridge emit."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location(
    "approve_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

approve_transition = _mod.approve_transition
handler = _mod.handler


def test_approve_requires_empty_review_queue() -> None:
    status, body = approve_transition("pending_review", review_queue_empty=False)
    assert status == 422
    assert body["error"] == "review_queue_not_empty"


def test_approve_from_pending_or_rejected() -> None:
    for state in ("pending_review", "rejected"):
        status, body = approve_transition(state, review_queue_empty=True)
        assert status == 200
        assert body["approval_state"] == "approved"


def test_approve_blocked_from_published() -> None:
    status, _ = approve_transition("published", review_queue_empty=True)
    assert status == 409


def _event() -> dict[str, Any]:
    return {
        "pathParameters": {"local": "150", "period": "2025-07-01"},
        "body": json.dumps({"approval_state": "pending_review", "review_queue_empty": True}),
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "approver-1"}}}},
    }


def test_approve_persists_and_emits(monkeypatch: Any) -> None:
    """audit B2: a successful approve must UPDATE Aurora AND PutEvents."""
    persisted: dict[str, Any] = {}
    emitted: dict[str, Any] = {}
    monkeypatch.setattr(
        _mod,
        "persist_approval",
        lambda local, period, approved_by: persisted.update(
            local=local, period=period, approved_by=approved_by
        ),
    )
    monkeypatch.setattr(
        _mod,
        "emit_event",
        lambda detail_type, detail: emitted.update(detail_type=detail_type, detail=detail),
    )
    result = handler(_event(), None)
    assert result["statusCode"] == 200
    assert persisted == {"local": "150", "period": "2025-07-01", "approved_by": "approver-1"}
    assert emitted["detail_type"] == "laboraid.rate-sheet.approved"
    assert emitted["detail"]["approved_by"] == "approver-1"


def test_approve_failure_neither_persists_nor_emits(monkeypatch: Any) -> None:
    calls: list[str] = []
    monkeypatch.setattr(_mod, "persist_approval", lambda *a: calls.append("persist"))
    monkeypatch.setattr(_mod, "emit_event", lambda *a: calls.append("emit"))
    event = _event()
    event["body"] = json.dumps({"approval_state": "published", "review_queue_empty": True})
    result = handler(event, None)
    assert result["statusCode"] == 409
    assert calls == []
