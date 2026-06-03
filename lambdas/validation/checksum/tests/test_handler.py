"""Tests for the checksum validator (Spec/09 §4 L6)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "checksum_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

validate = _mod.validate



def _canonical(notice_total: float) -> dict:
    return {
        "rows": [
            {
                "classification": "Journeyman",
                "notice_total": notice_total,
                "cells": {
                    "wage": {"value": 50.00, "canonical_field": "wage"},
                    "hw": {"value": 10.00, "canonical_field": "health_welfare"},
                    "pen": {"value": 8.50, "canonical_field": "pension"},
                },
            }
        ]
    }


def test_checksum_passes_within_tolerance() -> None:
    result = validate(_canonical(68.52))  # 50 + 10 + 8.50 = 68.50, diff 0.02
    assert result["passed"] is True
    assert not result["failures"]


def test_checksum_fails_outside_tolerance() -> None:
    result = validate(_canonical(70.00))  # diff 1.50 > 0.05
    assert result["passed"] is False
    assert len(result["failures"]) == 1


def test_checksum_skips_rows_without_total() -> None:
    canonical = _canonical(0.0)
    canonical["rows"][0]["notice_total"] = None
    result = validate(canonical)
    assert result["passed"] is True  # no checkable rows -> vacuously passes
    assert result["rows"][0]["passed"] is None
