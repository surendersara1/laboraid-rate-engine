"""Locks the rate_math rule against the values verified live (704, 2026-06-13).
If these change, an improved sheet would diverge from how the original was built."""

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambdas" / "shared"))

import rate_math  # noqa: E402


def test_derive_matches_live_704():
    # Foreman 56.82 and Apprentice Class 9 41.86 — exact stored values.
    assert rate_math.derive("56.82", 1.5) == Decimal("85.23")
    assert rate_math.derive("56.82", 2.0) == Decimal("113.64")
    assert rate_math.derive("56.82", 1.15) == Decimal("65.34")  # 65.343 -> half-up
    assert rate_math.derive("41.86", 1.5) == Decimal("62.79")
    assert rate_math.derive("41.86", 1.15) == Decimal("48.14")  # 48.139 -> half-up


def test_half_up_boundary():
    assert rate_math.derive("50.55", 1.5) == Decimal("75.83")  # 75.825 -> 75.83


def test_recompute_derived_uses_profile_multipliers():
    out = rate_math.recompute_derived(
        "56.82",
        {"Wage 1.5x": 1.5, "Wage 2.0x": 2.0, "Wage Differential": 1.15},
    )
    assert out == {
        "Wage 1.5x": Decimal("85.23"),
        "Wage 2.0x": Decimal("113.64"),
        "Wage Differential": Decimal("65.34"),
    }


def test_checksum_tolerance():
    assert rate_math.checksum_ok("100.00", "100.04")  # within 0.05
    assert not rate_math.checksum_ok("100.00", "100.10")
