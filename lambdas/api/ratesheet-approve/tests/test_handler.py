"""Tests for the approve transition."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "approve_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

approve_transition = _mod.approve_transition


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
