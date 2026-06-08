"""End-to-end smoke test for orchestrate.py — Bedrock helpers monkey-patched.

Verifies the full chain runs offline:

    analyze_groundtruth (real)
    → draft_profile_yaml (mocked → returns a canned profile)
    → draft_extractor_python (mocked → returns a canned extractor)
    → validate_generated (real schema_check + codegen_check + stubbed runner)
    → iterate_or_finalize (real heuristic)
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

import pytest

_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

import draft_extractor  # noqa: E402
import draft_profile  # noqa: E402
import orchestrate  # noqa: E402
import validate  # noqa: E402

CANNED_PROFILE = """\
union: sprinkler_fitters_120
constants:
  Union Group: UA
  Trade: Sprinkler
  Union Local: "120"
start_date: 1/1/26
end_date: 7/31/26
key_columns: [Zone, Package, Start Date, End Date]
columns:
  - Union Group
  - Trade
  - Union Local
  - Zone
  - Package
  - Start Date
  - End Date
  - {name: Wage, kind: $}
  - {name: Health & Welfare, kind: $}
"""

CANNED_EXTRACTOR = (
    "def extract_120(union_dir):\n" "    rows, gaps = [], []\n" "    return rows, gaps\n"
)


def _write_ratesheet(tmp_path: Path) -> Path:
    p = tmp_path / "rs.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "Union Group",
                "Trade",
                "Union Local",
                "Zone",
                "Package",
                "Start Date",
                "End Date",
                "Wage",
                "Health & Welfare",
            ]
        )
        w.writerow(
            [
                "UA",
                "Sprinkler",
                "120",
                "Building",
                "Journeyman",
                "1/1/26",
                "7/31/26",
                "54.70",
                "12.50",
            ]
        )
    return p


def test_orchestrate_finalizes_on_first_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stub LLM calls return canned outputs; stubbed evaluator returns high accuracy."""
    ratesheet = _write_ratesheet(tmp_path)
    cba_dir = tmp_path / "data" / "sprinkler_fitters_120"
    (cba_dir / "cba").mkdir(parents=True)
    # Drop a fake Rate Notice PDF so orchestrate finds something.
    (cba_dir / "cba" / "2026.01.01.120 Rate Notice.pdf").write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        draft_profile,
        "draft_profile_yaml",
        lambda *a, **kw: CANNED_PROFILE,
    )
    monkeypatch.setattr(
        draft_extractor,
        "draft_extractor_python",
        lambda *a, **kw: CANNED_EXTRACTOR,
    )
    monkeypatch.setattr(
        orchestrate,
        "draft_profile_yaml",
        lambda *a, **kw: CANNED_PROFILE,
    )
    monkeypatch.setattr(
        orchestrate,
        "draft_extractor_python",
        lambda *a, **kw: CANNED_EXTRACTOR,
    )

    def fake_runner(*args: Any, **kwargs: Any) -> tuple[str, str]:
        return (
            "=== OVERALL CELL ACCURACY: 95/100 = 95.0%  (blanks 2, wrong 3) ===",
            "",
        )

    monkeypatch.setattr(validate, "_run_evaluator", fake_runner)

    result = orchestrate.orchestrate(
        union_key="sprinkler_fitters_120",
        cba_dir=str(cba_dir),
        ratesheet_path=str(ratesheet),
        scratch_root=str(tmp_path / "scratch"),
    )

    assert result["status"] == "drafted"
    assert result["iterations"] == 1
    assert result["validation"]["schema_pass"] is True
    assert result["validation"]["codegen_pass"] is True
    assert result["validation"]["accuracy_pct"] == 95.0
    assert "extract_120" in result["extractor_py"]
    assert "union: sprinkler_fitters_120" in result["profile_yaml"]


def test_orchestrate_escalates_after_max_iterations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stubbed evaluator returns low accuracy forever; loop must escalate."""
    ratesheet = _write_ratesheet(tmp_path)
    cba_dir = tmp_path / "data" / "sprinkler_fitters_120"
    (cba_dir / "cba").mkdir(parents=True)

    monkeypatch.setattr(
        orchestrate,
        "draft_profile_yaml",
        lambda *a, **kw: CANNED_PROFILE,
    )
    monkeypatch.setattr(
        orchestrate,
        "draft_extractor_python",
        lambda *a, **kw: CANNED_EXTRACTOR,
    )

    monkeypatch.setattr(
        validate,
        "_run_evaluator",
        lambda *args, **kwargs: (
            "=== OVERALL CELL ACCURACY: 10/100 = 10.0%  (blanks 50, wrong 40) ===",
            "",
        ),
    )

    result = orchestrate.orchestrate(
        union_key="sprinkler_fitters_120",
        cba_dir=str(cba_dir),
        ratesheet_path=str(ratesheet),
        max_iterations=2,
        scratch_root=str(tmp_path / "scratch"),
    )

    assert result["status"] == "escalated"
    assert result["iterations"] >= 2


def test_orchestrate_propagates_no_creds_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When neither AWS nor Anthropic creds are present, the helper raises."""
    ratesheet = _write_ratesheet(tmp_path)
    cba_dir = tmp_path / "data" / "sprinkler_fitters_120"
    (cba_dir / "cba").mkdir(parents=True)

    # Force the helpers down the no-creds path.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", raising=False)
    monkeypatch.delenv("AWS_WEB_IDENTITY_TOKEN_FILE", raising=False)
    monkeypatch.setattr("os.path.exists", lambda _p: False)

    with pytest.raises(RuntimeError, match="No LLM creds"):
        orchestrate.orchestrate(
            union_key="sprinkler_fitters_120",
            cba_dir=str(cba_dir),
            ratesheet_path=str(ratesheet),
            scratch_root=str(tmp_path / "scratch"),
        )
