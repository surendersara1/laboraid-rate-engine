"""Tests for analyze_groundtruth (E.1)."""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

import pytest

_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from analyze import analyze_groundtruth  # noqa: E402

# ---------------------------------------------------------------------------
# Real-file test — kernel/data/sprinkler_fitters_704/ratesheet/2026.01.01.704 Rate Sheet.csv
# ---------------------------------------------------------------------------

REPO_ROOT = _AGENT_DIR.parent.parent
REAL_RATESHEET = (
    REPO_ROOT
    / "kernel"
    / "data"
    / "sprinkler_fitters_704"
    / "ratesheet"
    / "2026.01.01.704 Rate Sheet.csv"
)


@pytest.mark.skipif(not REAL_RATESHEET.exists(), reason="kernel data not present")
def test_real_704_ratesheet_analyzed() -> None:
    """Analyze the actual 704 ratesheet; verify shape + canonical mapping."""
    result = analyze_groundtruth(str(REAL_RATESHEET))

    # Top-level keys.
    assert set(result.keys()) == {"columns", "key_columns", "sample_rows", "unknown_fields"}

    # Key columns: the 7 reference key labels.
    expected_keys = {
        "Union Group",
        "Trade",
        "Union Local",
        "Zone",
        "Package",
        "Start Date",
        "End Date",
    }
    assert expected_keys.issubset(set(result["key_columns"]))

    # The first 7 column entries echo the key-column names.
    first_seven_names = [c["name"] for c in result["columns"][:7]]
    assert first_seven_names == [
        "Union Group",
        "Trade",
        "Union Local",
        "Zone",
        "Package",
        "Start Date",
        "End Date",
    ]

    # Wage is present, classified as $, and maps to canonical 'wage'.
    by_name: dict[str, dict[str, Any]] = {c["name"]: c for c in result["columns"]}
    assert "Wage" in by_name
    assert by_name["Wage"]["kind"] == "$"
    assert by_name["Wage"].get("canonical_field") == "wage"

    # Wage 1.5x / Wage 2.0x are $ as well.
    assert by_name["Wage 1.5x"]["kind"] == "$"
    assert by_name["Wage 2.0x"]["kind"] == "$"

    # Health & Welfare → canonical health_welfare.
    assert by_name["Health & Welfare"].get("canonical_field") == "health_welfare"
    # S & E 704 → canonical se_fund.
    assert by_name["S & E 704"].get("canonical_field") == "se_fund"

    # Sample rows: at least 1, dict keyed by column.
    assert len(result["sample_rows"]) >= 1
    sample = result["sample_rows"][0]
    assert sample["Zone"] == "Building"
    # First sample row is the General Foreman row in the actual 704 CSV.
    assert sample["Package"] == "General Foreman"

    # unknown_fields should be empty — every 704 column maps to a canonical.
    assert isinstance(result["unknown_fields"], list)


# ---------------------------------------------------------------------------
# Synthetic-CSV tests (don't require kernel data).
# ---------------------------------------------------------------------------


def _write_csv(tmp_path: Path, name: str, rows: list[list[str]]) -> Path:
    p = tmp_path / name
    with p.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        for r in rows:
            writer.writerow(r)
    return p


def test_synthetic_classifies_kinds(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path,
        "test.csv",
        [
            ["Zone", "Package", "Wage", "Dues Pct", "Note"],
            ["Building", "Journeyman", "54.70", "6.00%", "ok"],
            ["Building", "Foreman", "60.20", "6.00%", "ok"],
        ],
    )
    result = analyze_groundtruth(str(csv_path))
    by_name = {c["name"]: c for c in result["columns"]}
    assert by_name["Wage"]["kind"] == "$"
    assert by_name["Dues Pct"]["kind"] == "%"
    assert by_name["Note"]["kind"] == "raw"
    # Zone + Package are key columns.
    assert "Zone" in result["key_columns"]
    assert "Package" in result["key_columns"]


def test_unknown_field_flagged(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path,
        "test.csv",
        [
            ["Zone", "Package", "Wage", "Brand New Fund 999"],
            ["Building", "Journeyman", "54.70", "1.23"],
        ],
    )
    result = analyze_groundtruth(str(csv_path))
    assert "Brand New Fund 999" in result["unknown_fields"]
    # Known columns are NOT in unknown_fields.
    assert "Wage" not in result["unknown_fields"]
    assert "Zone" not in result["unknown_fields"]


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        analyze_groundtruth(str(tmp_path / "nope.csv"))


def test_unsupported_extension_raises(tmp_path: Path) -> None:
    bad = tmp_path / "x.txt"
    bad.write_text("dummy", encoding="utf-8")
    with pytest.raises(ValueError):
        analyze_groundtruth(str(bad))


def test_sample_rows_capped_to_three(tmp_path: Path) -> None:
    rows = [["Zone", "Wage"]]
    for i in range(10):
        rows.append(["Building", str(10 + i)])
    csv_path = _write_csv(tmp_path, "many.csv", rows)
    result = analyze_groundtruth(str(csv_path))
    assert len(result["sample_rows"]) == 3
