"""Tests for the unapprove transition."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "unapprove_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

unapprove_transition = _mod.unapprove_transition


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
