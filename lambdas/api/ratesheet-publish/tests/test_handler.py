"""Tests for the publish guard (SOW contract: 409 unless approved)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "publish_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

publish_guard = _mod.publish_guard


def test_publish_blocked_unless_approved() -> None:
    for state in ("pending_review", "rejected", "published"):
        status, body = publish_guard(state)
        assert status == 409
        assert body["error"] == "not_approved"


def test_publish_allowed_when_approved() -> None:
    status, body = publish_guard("approved")
    assert status == 200
    assert body["approval_state"] == "published"
