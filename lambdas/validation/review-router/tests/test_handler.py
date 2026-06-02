"""Tests for the review router (pure item-building)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "review_router_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

build_review_items = _mod.build_review_items



def test_build_review_items() -> None:
    items = build_review_items(
        tenant="laboraid",
        union="sprinkler_fitters_704",
        period="2026-01-01",
        created_at="2026-06-02T12:00:00Z",
        low_confidence_cells=[
            {"classification": "Journeyman", "field": "wage", "confidence": 0.6},
            {"classification": "Apprentice", "field": "pension", "confidence": 0.7},
        ],
    )
    assert len(items) == 2
    assert items[0]["tenant"] == "laboraid"
    assert items[0]["status"] == "pending"
    assert items[0]["created_at#cell_id"].startswith("2026-06-02T12:00:00Z#")


def test_empty_when_no_cells() -> None:
    items = build_review_items(
        tenant="laboraid",
        union="u",
        period="p",
        created_at="t",
        low_confidence_cells=[],
    )
    assert items == []
