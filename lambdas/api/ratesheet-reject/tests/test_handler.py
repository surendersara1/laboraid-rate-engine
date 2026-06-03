"""Tests for the reject transition + Aurora persistence + EventBridge emit."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location(
    "reject_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

reject_transition = _mod.reject_transition
handler = _mod.handler


def test_reject_requires_reason() -> None:
    status, body = reject_transition("pending_review", "")
    assert status == 422
    assert body["error"] == "reason_required"


def test_reject_with_reason() -> None:
    status, body = reject_transition("pending_review", "wrong wage on JW row")
    assert status == 200
    assert body["approval_state"] == "rejected"


def test_reject_rejects_invalid_tags() -> None:
    status, body = reject_transition("pending_review", "bad", ["nonsense"])
    assert status == 422
    assert body["error"] == "invalid_tags"


def _event() -> dict[str, Any]:
    return {
        "pathParameters": {"local": "150", "period": "2025-07-01"},
        "body": json.dumps(
            {"approval_state": "pending_review", "reason": "wrong wage", "tags": ["missing_data"]}
        ),
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "rejecter-1"}}}},
    }


def test_reject_persists_and_emits(monkeypatch: Any) -> None:
    """audit B2: a successful reject must UPDATE Aurora (reason+tags) AND PutEvents."""
    persisted: dict[str, Any] = {}
    emitted: dict[str, Any] = {}
    monkeypatch.setattr(
        _mod,
        "persist_rejection",
        lambda local, period, rejected_by, reason, tags: persisted.update(
            local=local, period=period, rejected_by=rejected_by, reason=reason, tags=tags
        ),
    )
    monkeypatch.setattr(
        _mod,
        "emit_event",
        lambda detail_type, detail: emitted.update(detail_type=detail_type, detail=detail),
    )
    result = handler(_event(), None)
    assert result["statusCode"] == 200
    assert persisted["rejected_by"] == "rejecter-1"
    assert persisted["reason"] == "wrong wage"
    assert persisted["tags"] == ["missing_data"]
    assert emitted["detail_type"] == "laboraid.rate-sheet.rejected"


def test_reject_failure_neither_persists_nor_emits(monkeypatch: Any) -> None:
    calls: list[str] = []
    monkeypatch.setattr(_mod, "persist_rejection", lambda *a: calls.append("persist"))
    monkeypatch.setattr(_mod, "emit_event", lambda *a: calls.append("emit"))
    event = _event()
    event["body"] = json.dumps({"approval_state": "pending_review", "reason": ""})
    result = handler(event, None)
    assert result["statusCode"] == 422
    assert calls == []
