"""Tests for the Slack notifier's pure message formatting."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "slack_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

format_slack_message = _mod.format_slack_message


def test_format_failure() -> None:
    payload = format_slack_message(
        {
            "event": "laboraid.job.failed",
            "union_local": 704,
            "period": "2026-07-01",
            "stage": "l4_extract",
            "error": {"type": "ExtractionConfidenceTooLow", "message": "0.62 < 0.85"},
            "links": {"review_url": "https://admin.laboraid.app/review/j-1"},
        }
    )
    assert ":red_circle:" in payload["text"]
    assert "union 704" in payload["text"]
    assert "Open review" in payload["text"]


def test_format_unknown_event() -> None:
    payload = format_slack_message({"event": "laboraid.something"})
    assert ":information_source:" in payload["text"]
