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


def rmul(base, factor) -> float:
    """base * factor, rounded half-up to 2dp, with the product taken IN Decimal.

    Multiplying floats first loses precision and rounds the .x5 boundary the wrong
    way: ``50.55 * 1.5`` is ``75.82499999999999`` in float, so ``r2(base*factor)``
    yields 75.82 -- but the ratesheets carry 75.83. Likewise ``37.90 * 1.15`` ->
    43.59 (float gives 43.58). Doing the product in Decimal preserves the exact
    .825 / .585 half so ROUND_HALF_UP rounds it correctly. Use this for every
    derived multiplier column (Wage 1.1x/1.5x/2.0x, Wage Differential, Temporary
    Heat 1.1x/1.5x, etc.).

    Note: the *source* ratesheets are not internally consistent about this -- some
    locals' spreadsheets round the .x5 half down via Excel binary float (e.g. 537's
    74.115 -> 74.11). Those differences are within the evaluator's +/-0.01
    tolerance; this helper standardises on mathematically-correct half-up.
    """
    if base is None or factor is None:
        return None
    return float((Decimal(str(base)) * Decimal(str(factor))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP))


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
