"""Tests for the reject transition."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "reject_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

reject_transition = _mod.reject_transition


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
