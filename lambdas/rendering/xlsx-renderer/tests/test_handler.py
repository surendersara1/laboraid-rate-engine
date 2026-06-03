"""Tests for the xlsx renderer's pure CSV parsing."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "xlsx_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

parse_csv = _mod.parse_csv


def test_parse_csv() -> None:
    header, rows = parse_csv("A,B,C\n1,2,3\n4,5,6\n")
    assert header == ["A", "B", "C"]
    assert rows == [["1", "2", "3"], ["4", "5", "6"]]


def test_parse_empty() -> None:
    assert parse_csv("") == ([], [])
