"""Unit tests for the half-up rounding helpers and the profile multiplier path.

These guard the bug fixed in canonical.model.rmul / pipeline.compute: derived
multiplier columns must multiply IN Decimal, not float-first, so the .x5 boundary
rounds up correctly (50.55 x 1.5 -> 75.83, not 75.82).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KERNEL = os.path.dirname(HERE)
sys.path.insert(0, KERNEL)

from canonical.model import ClassificationRow, RateCell, r2, rmul  # noqa: E402
from pipeline.compute import resolve_row  # noqa: E402


def test_r2_half_up():
    assert r2(0.125) == 0.13
    assert r2(2.005) == 2.01
    assert r2(None) is None


def test_rmul_rounds_half_up_at_boundary():
    # the exact cases that float-first multiply gets wrong
    assert rmul(50.55, 1.5) == 75.83        # 75.825 -> up
    assert rmul(37.90, 1.15) == 43.59       # 43.585 -> up
    assert rmul(34.80, 1.5) == 52.20
    assert rmul(28.45, 1.15) == 32.72


def test_float_first_multiply_is_the_bug_rmul_avoids():
    # documents WHY rmul exists: r2(base*factor) multiplies in float first and
    # rounds the half the wrong way. This asserts the broken behaviour so nobody
    # "simplifies" rmul back to r2(base*factor).
    assert r2(50.55 * 1.5) == 75.82         # WRONG (the old bug)
    assert rmul(50.55, 1.5) == 75.83        # RIGHT (the fix)


def test_rmul_none_safe():
    assert rmul(None, 1.5) is None
    assert rmul(10.0, None) is None


def test_profile_multiplier_column_uses_decimal_multiply():
    """The compute stage must produce 75.83 for Wage 1.5x of a 50.55 wage."""
    profile = {
        "constants": {},
        "start_date": "1/1/26",
        "end_date": "6/30/26",
        "columns": [
            {"name": "Wage", "kind": "$"},
            {"name": "Wage 1.5x", "kind": "$", "multiplier_of": "Wage", "factor": 1.5},
        ],
    }
    row = ClassificationRow("Building", "Journeyman", 90)
    row.add(RateCell("Building", "Journeyman", 90, "wage", 50.55, "$"))
    out = resolve_row(profile, row)
    assert out["Wage"] == 50.55
    assert out["Wage 1.5x"] == 75.83
