"""Canonical intermediate model + half-up rounding helper.

The pipeline maps every union's CBA documents into a tidy/long list of
`RateCell` records (one record per zone x classification x canonical field),
then a per-union profile pivots them into the wide groundtruth-shaped CSV.

Reused from `extract/build_483.py`: the `r2()` half-up rounding (proven
necessary: 83.505 -> 83.51, not banker's 83.50).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


def r2(x) -> float:
    """Round to 2 decimals, half-up (matches how the ratesheets are rounded)."""
    if x is None:
        return None
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass
class RateCell:
    """One value in the canonical long model."""
    zone: str
    classification: str          # Package, e.g. "Journeyman", "Apprentice Class 3"
    class_order: int             # for ordering rows within a zone (descending pay)
    canonical_field: str         # e.g. "wage", "health_welfare"
    value: Optional[object]      # float for $; str like "6.00%" for %; None if unsourced
    value_kind: str = "$"        # "$" | "%" | "xN" | "raw"
    source_doc: str = ""
    source_locator: str = ""
    confidence: float = 1.0


@dataclass
class ClassificationRow:
    """All canonical cells for one (zone, classification) row."""
    zone: str
    classification: str
    class_order: int
    cells: dict = field(default_factory=dict)  # canonical_field -> RateCell

    def add(self, cell: RateCell):
        self.cells[cell.canonical_field] = cell

    def get(self, canonical_field):
        c = self.cells.get(canonical_field)
        return None if c is None else c.value
