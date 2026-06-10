"""Tests for the dual-control approve state machine (Move 6).

State transitions:
  pending_review | rejected -> pending_approval (stage=review)
  pending_approval         -> approved          (stage=approve, by a different actor)
  approved | published     -> 409
"""

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


def test_review_queue_blocks_review() -> None:
    status, body = approve_transition(
        "pending_review", review_queue_empty=False, actor="alice", reviewed_by=None
    )
    assert status == 422
    assert body["error"] == "review_queue_not_empty"


def test_pending_review_advances_to_pending_approval() -> None:
    for state in ("pending_review", "rejected"):
        status, body = approve_transition(
            state, review_queue_empty=True, actor="alice", reviewed_by=None
        )
        assert status == 200
        assert body["approval_state"] == "pending_approval"
        assert body["stage"] == "review"


def test_pending_approval_advances_to_approved() -> None:
    status, body = approve_transition(
        "pending_approval", review_queue_empty=True, actor="bob", reviewed_by="alice"
    )
    assert status == 200
    assert body["approval_state"] == "approved"
    assert body["stage"] == "approve"


def test_dual_control_blocks_same_actor() -> None:
    """The reviewer cannot also be the approver."""
    status, body = approve_transition(
        "pending_approval", review_queue_empty=True, actor="alice", reviewed_by="alice"
    )
    assert status == 409
    assert body["error"] == "dual_control_violation"


def test_approved_terminal() -> None:
    status, _ = approve_transition(
        "approved", review_queue_empty=True, actor="bob", reviewed_by="alice"
    )
    assert status == 409


def test_published_terminal() -> None:
    status, _ = approve_transition(
        "published", review_queue_empty=True, actor="bob", reviewed_by="alice"
    )
    assert status == 409


def _event(actor: str = "approver-1") -> dict[str, Any]:
    return {
        "pathParameters": {"local": "150", "period": "2025-07-01"},
        "body": json.dumps({"review_queue_empty": True}),
        "requestContext": {
            "authorizer": {
                "jwt": {"claims": {"sub": actor, "cognito:groups": "[Business]"}}
            }
        },
    }


def test_review_persists_and_emits(monkeypatch: Any) -> None:
    """Stage 1: first actor on pending_review marks it reviewed."""
    persisted: dict[str, Any] = {}
    emitted: dict[str, Any] = {}
    monkeypatch.setattr(_mod, "fetch_current", lambda l, p: ("pending_review", None))
    monkeypatch.setattr(
        _mod,
        "persist_review",
        lambda local, period, reviewed_by: persisted.update(
            stage="review", local=local, period=period, by=reviewed_by
        ),
    )
    monkeypatch.setattr(_mod, "persist_approval", lambda *a: persisted.setdefault("oops", True))
    monkeypatch.setattr(
        _mod,
        "emit_event",
        lambda detail_type, detail: emitted.update(detail_type=detail_type, detail=detail),
    )
    result = handler(_event("alice"), None)
    body = json.loads(result["body"])
    assert result["statusCode"] == 200
    assert body["approval_state"] == "pending_approval"
    assert body["reviewed_by"] == "alice"
    assert persisted["stage"] == "review"
    assert "oops" not in persisted
    assert emitted["detail_type"] == "laboraid.rate-sheet.reviewed"


def test_approve_persists_and_emits(monkeypatch: Any) -> None:
    """Stage 2: a different actor on pending_approval approves."""
    persisted: dict[str, Any] = {}
    emitted: dict[str, Any] = {}
    monkeypatch.setattr(_mod, "fetch_current", lambda l, p: ("pending_approval", "alice"))
    monkeypatch.setattr(_mod, "persist_review", lambda *a: persisted.setdefault("oops", True))
    monkeypatch.setattr(
        _mod,
        "persist_approval",
        lambda local, period, approved_by: persisted.update(
            stage="approve", local=local, period=period, by=approved_by
        ),
    )
    monkeypatch.setattr(
        _mod,
        "emit_event",
        lambda detail_type, detail: emitted.update(detail_type=detail_type, detail=detail),
    )
    result = handler(_event("bob"), None)
    body = json.loads(result["body"])
    assert result["statusCode"] == 200
    assert body["approval_state"] == "approved"
    assert body["approved_by"] == "bob"
    assert persisted["stage"] == "approve"
    assert "oops" not in persisted
    assert emitted["detail_type"] == "laboraid.rate-sheet.approved"


def test_approve_blocked_by_dual_control(monkeypatch: Any) -> None:
    """Same person cannot do both stages."""
    calls: list[str] = []
    monkeypatch.setattr(_mod, "fetch_current", lambda l, p: ("pending_approval", "alice"))
    monkeypatch.setattr(_mod, "persist_review", lambda *a: calls.append("review"))
    monkeypatch.setattr(_mod, "persist_approval", lambda *a: calls.append("approve"))
    monkeypatch.setattr(_mod, "emit_event", lambda *a: calls.append("emit"))
    result = handler(_event("alice"), None)
    assert result["statusCode"] == 409
    assert json.loads(result["body"])["error"] == "dual_control_violation"
    assert calls == []


def test_authz_forbidden_when_group_empty() -> None:
    event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"cognito:groups": "[]"}}}},
        "body": "{}",
    }
    result = _mod.handler(event, None)
    assert result["statusCode"] == 403


def test_authz_forbidden_when_group_missing() -> None:
    event = {"requestContext": {"authorizer": {"jwt": {"claims": {}}}}, "body": "{}"}
    result = _mod.handler(event, None)
    assert result["statusCode"] == 403
