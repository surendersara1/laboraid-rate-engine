"""Tests for iterate.py (E.5)."""

from __future__ import annotations

import sys
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from iterate import iterate_or_finalize  # noqa: E402


def _vr(schema: bool, codegen: bool, accuracy: float) -> dict[str, object]:
    return {
        "schema_pass": schema,
        "codegen_pass": codegen,
        "accuracy_pct": accuracy,
    }


def test_schema_fail_returns_regenerate_profile() -> None:
    out = iterate_or_finalize("u", 1, _vr(False, False, 0.0))
    assert out == "regenerate_profile"


def test_schema_pass_codegen_fail_returns_regenerate_extractor() -> None:
    out = iterate_or_finalize("u", 1, _vr(True, False, 0.0))
    assert out == "regenerate_extractor"


def test_both_pass_low_accuracy_returns_regenerate_extractor() -> None:
    out = iterate_or_finalize("u", 1, _vr(True, True, 50.0))
    assert out == "regenerate_extractor"


def test_both_pass_high_accuracy_returns_finalize() -> None:
    out = iterate_or_finalize("u", 1, _vr(True, True, 85.0))
    assert out == "finalize"


def test_threshold_boundary_inclusive_at_threshold() -> None:
    out = iterate_or_finalize("u", 1, _vr(True, True, 70.0))
    assert out == "finalize"


def test_max_iterations_triggers_escalate() -> None:
    out = iterate_or_finalize("u", 3, _vr(True, True, 50.0))
    assert out == "escalate"


def test_custom_threshold_respected() -> None:
    # accuracy 75 with custom threshold 80 → still regenerate
    out = iterate_or_finalize(
        "u", 1, _vr(True, True, 75.0), accuracy_threshold=80.0
    )
    assert out == "regenerate_extractor"


def test_custom_max_iterations_respected() -> None:
    # With max_iterations=2, drafts_so_far=2 triggers escalate.
    out = iterate_or_finalize(
        "u", 2, _vr(True, True, 50.0), max_iterations=2
    )
    assert out == "escalate"


def test_escalate_wins_over_other_actions() -> None:
    # Even with a perfect-looking validation, hitting the ceiling escalates.
    out = iterate_or_finalize("u", 5, _vr(False, False, 0.0))
    assert out == "escalate"
