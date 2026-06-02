"""Tests for the CSV renderer header validation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "csv_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

validate_header = _mod.validate_header


def test_valid_header() -> None:
    text = "Union Group,Trade,Union Local,Zone,Package,Wage\nUA,Sprinkler,704,Building,JW,50\n"
    result = validate_header(text)
    assert result["valid"] is True
    assert result["missing"] == []


def test_missing_columns() -> None:
    result = validate_header("Foo,Bar\n1,2\n")
    assert result["valid"] is False
    assert "Union Group" in result["missing"]
