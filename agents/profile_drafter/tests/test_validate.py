"""Tests for validate.py (E.4)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

import validate  # noqa: E402


VALID_PROFILE = """\
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
"""

VALID_EXTRACTOR = "def extract_120(union_dir):\n    return [], []\n"


def test_missing_profile_yields_error(tmp_path: Path) -> None:
    result = validate.validate_generated(
        str(tmp_path / "no.yaml"),
        str(tmp_path / "no.py"),
        str(tmp_path),
        str(tmp_path / "gt.csv"),
    )
    assert result["schema_pass"] is False
    assert any("could not read profile" in e for e in result["errors"])


def test_bad_profile_fails_schema(tmp_path: Path) -> None:
    profile = tmp_path / "p.yaml"
    profile.write_text("not: a valid: profile", encoding="utf-8")
    extractor = tmp_path / "e.py"
    extractor.write_text(VALID_EXTRACTOR, encoding="utf-8")
    result = validate.validate_generated(
        str(profile), str(extractor), str(tmp_path), str(tmp_path / "gt.csv")
    )
    assert result["schema_pass"] is False


def test_bad_extractor_fails_codegen(tmp_path: Path) -> None:
    profile = tmp_path / "p.yaml"
    profile.write_text(VALID_PROFILE, encoding="utf-8")
    extractor = tmp_path / "e.py"
    extractor.write_text("def extract_120(x): return 1\n", encoding="utf-8")
    result = validate.validate_generated(
        str(profile), str(extractor), str(tmp_path), str(tmp_path / "gt.csv")
    )
    assert result["schema_pass"] is True
    assert result["codegen_pass"] is False
    # No evaluator run since one of the gates failed.
    assert result["evaluator_output"] == ""


def test_infer_union_key_from_yaml() -> None:
    assert validate._infer_union_key(VALID_PROFILE, "/x") == "sprinkler_fitters_120"


def test_infer_union_key_fallback_to_dirname() -> None:
    assert validate._infer_union_key("", "/data/sprinkler_fitters_120") == (
        "sprinkler_fitters_120"
    )


def test_accuracy_regex_parses_evaluator_line() -> None:
    line = "=== OVERALL CELL ACCURACY: 50/100 = 50.0%  (blanks 30, wrong 20) ==="
    m = validate._ACC_RE.search(line)
    assert m is not None
    assert float(m.group(3)) == 50.0
    assert int(m.group(4)) == 30
    assert int(m.group(5)) == 20


def test_skip_evaluator_when_either_gate_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If schema_check fails, _run_evaluator must NOT be called."""
    called = {"n": 0}

    def stub_runner(*args: object, **kwargs: object) -> tuple[str, str]:
        called["n"] += 1
        return "", ""

    monkeypatch.setattr(validate, "_run_evaluator", stub_runner)
    profile = tmp_path / "p.yaml"
    profile.write_text("garbage", encoding="utf-8")
    extractor = tmp_path / "e.py"
    extractor.write_text(VALID_EXTRACTOR, encoding="utf-8")
    validate.validate_generated(
        str(profile), str(extractor), str(tmp_path), str(tmp_path / "gt.csv")
    )
    assert called["n"] == 0


def test_evaluator_called_with_stub_returns_accuracy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "p.yaml"
    profile.write_text(VALID_PROFILE, encoding="utf-8")
    extractor = tmp_path / "e.py"
    extractor.write_text(VALID_EXTRACTOR, encoding="utf-8")

    def stub_runner(*args: object, **kwargs: object) -> tuple[str, str]:
        return (
            "=== OVERALL CELL ACCURACY: 42/100 = 42.0%  (blanks 10, wrong 48) ===",
            "",
        )

    monkeypatch.setattr(validate, "_run_evaluator", stub_runner)
    result = validate.validate_generated(
        str(profile), str(extractor), str(tmp_path), str(tmp_path / "gt.csv")
    )
    assert result["schema_pass"] is True
    assert result["codegen_pass"] is True
    assert result["accuracy_pct"] == 42.0
    assert result["mismatch_count"] == 58  # blanks + wrong
