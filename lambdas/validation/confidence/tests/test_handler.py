"""Tests for the confidence rollup validator."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "confidence_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

validate = _mod.validate



def test_all_high_confidence_passes() -> None:
    canonical = {
        "rows": [
            {
                "classification": "Journeyman",
                "cells": {
                    "wage": {"value": 50.0, "canonical_field": "wage", "confidence": 0.99},
                    "hw": {"value": 10.0, "canonical_field": "health_welfare", "confidence": 0.95},
                },
            }
        ]
    }
    result = validate(canonical)
    assert result["passed"] is True
    assert result["mean_confidence"] == 0.97


def test_low_confidence_flagged() -> None:
    canonical = {
        "rows": [
            {
                "classification": "Journeyman",
                "cells": {
                    "wage": {"value": 50.0, "canonical_field": "wage", "confidence": 0.60},
                },
            }
        ]
    }
    result = validate(canonical)
    assert result["passed"] is False
    assert result["low_confidence_cells"][0]["confidence"] == 0.60
