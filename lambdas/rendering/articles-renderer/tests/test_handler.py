"""Tests for the articles renderer's gaps.md parsing."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "articles_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

parse_gaps_md = _mod.parse_gaps_md
to_articles_csv = _mod.to_articles_csv

_SAMPLE = """# Unsourced / divergent cells - pipe_fitters_537

Some prose that should be ignored.

| Zone | Package | Column | Reason |
|---|---|---|---|
| * | * | Wage / Pension | 3/1/2026 re-allocation not stated in the books. |
| Building | Foreman | Annuity | value absent from CBA docs |
"""


def test_parse_gaps_md() -> None:
    entries = parse_gaps_md(_SAMPLE)
    assert len(entries) == 2
    assert entries[0]["zone"] == "*"
    assert entries[1]["column"] == "Annuity"
    assert "absent" in entries[1]["reason"]


def test_to_articles_csv_roundtrip() -> None:
    entries = parse_gaps_md(_SAMPLE)
    csv_text = to_articles_csv(entries)
    assert csv_text.splitlines()[0] == "zone,package,column,reason"
    assert len(csv_text.splitlines()) == 3  # header + 2 rows
