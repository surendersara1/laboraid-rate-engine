"""rate_math — the single source of truth for rate-sheet arithmetic.

Both the synthesizer (Lambda) and the Phase-2 improver (AgentCore agent) MUST use
this module so a re-derived / improved sheet is byte-identical to how the original
was produced. Pure (no AWS / no I/O) → unit-testable and importable anywhere.

Verified rule (confirmed against the live 704 sheet 2026-06-13):
    derived_value = round_half_up(Decimal(base) * Decimal(multiplier), 2)
e.g. 56.82 * 1.5 -> 85.23 ; 56.82 * 1.15 -> 65.343 -> 65.34
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Mapping

_CENT = Decimal("0.01")


def r2(x: Decimal | float | str) -> Decimal:
    """Round to cents, half-up (the client's convention)."""
    return Decimal(str(x)).quantize(_CENT, rounding=ROUND_HALF_UP)


def derive(base: Decimal | float | str, multiplier: Decimal | float | str) -> Decimal:
    """A single derived wage column: base * multiplier, rounded to cents half-up."""
    return r2(Decimal(str(base)) * Decimal(str(multiplier)))


def recompute_derived(
    base_wage: Decimal | float | str,
    multipliers: Mapping[str, float | str | Decimal],
) -> dict[str, Decimal]:
    """Given a base Wage and the union profile's `derived_multipliers`
    ({"Wage 1.5x": 1.5, "Wage 2.0x": 2.0, "Wage Differential": 1.15}), return
    {derived_column_name: value}. The ONLY place derived wage columns are computed.
    """
    return {col: derive(base_wage, factor) for col, factor in multipliers.items()}


def total_package(values: Iterable[Decimal | float | str]) -> Decimal:
    """Sum of a row's wage + all fringe contributions, to cents."""
    total = Decimal("0")
    for v in values:
        if v is None:
            continue
        total += Decimal(str(v))
    return r2(total)


def checksum_ok(
    computed_total: Decimal | float | str,
    stated_total: Decimal | float | str,
    tolerance: Decimal | float | str = "0.05",
) -> bool:
    """Total-package gate: wage + fringes must equal the printed Total Package
    within tolerance (default ±$0.05). Used by the improver's validate tool and
    (eventually) the synthesizer's pre-publish check."""
    return abs(Decimal(str(computed_total)) - Decimal(str(stated_total))) <= Decimal(
        str(tolerance)
    )
