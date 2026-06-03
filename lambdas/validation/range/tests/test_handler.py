"""Tests for the range validator."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "range_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

validate = _mod.validate



def test_in_range_passes() -> None:
    canonical = {
        "rows": [
            {
                "classification": "Journeyman",
                "cells": {
                    "wage": {"value": 50.0, "canonical_field": "wage"},
                    "hw": {"value": 10.0, "canonical_field": "health_welfare"},
                },
            }
        ]
    }
    assert validate(canonical)["passed"] is True


def test_out_of_range_flagged() -> None:
    canonical = {
        "rows": [
            {
                "classification": "Journeyman",
                "cells": {
                    "wage": {"value": 500.0, "canonical_field": "wage"},  # > 200
                    "hw": {"value": 99.0, "canonical_field": "health_welfare"},  # > 30
                },
            }
        ]
    }
    result = validate(canonical)
    assert result["passed"] is False
    assert len(result["violations"]) == 2
