"""Tests for schema_check.py (D.3)."""

from __future__ import annotations

import sys
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from schema_check import schema_check  # noqa: E402

# ---------------------------------------------------------------------------
# Valid YAML — must pass.
# ---------------------------------------------------------------------------


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
  - {name: Wage 1.5x, kind: $, multiplier_of: Wage, factor: 1.5}
  - {name: Health & Welfare, kind: $}
"""


def test_valid_profile_passes_schema_check() -> None:
    result = schema_check(VALID_PROFILE)
    assert result["ok"] is True, f"errors: {result['errors']}"
    assert result["errors"] == []


def test_reference_704_profile_passes() -> None:
    """The actual kernel/profiles/sprinkler_fitters_704.yaml must validate."""
    repo_root = _AGENT_DIR.parent.parent
    ref = repo_root / "kernel" / "profiles" / "sprinkler_fitters_704.yaml"
    text = ref.read_text(encoding="utf-8")
    result = schema_check(text)
    assert result["ok"] is True, f"reference profile failed schema: {result['errors']}"


def test_reference_483_profile_passes() -> None:
    repo_root = _AGENT_DIR.parent.parent
    ref = repo_root / "kernel" / "profiles" / "sprinkler_fitters_483.yaml"
    text = ref.read_text(encoding="utf-8")
    result = schema_check(text)
    assert result["ok"] is True, f"reference profile failed schema: {result['errors']}"


def test_reference_537_profile_passes() -> None:
    repo_root = _AGENT_DIR.parent.parent
    ref = repo_root / "kernel" / "profiles" / "pipe_fitters_537.yaml"
    text = ref.read_text(encoding="utf-8")
    result = schema_check(text)
    assert result["ok"] is True, f"reference profile failed schema: {result['errors']}"


# ---------------------------------------------------------------------------
# Invalid YAMLs — must fail with informative errors.
# ---------------------------------------------------------------------------


def test_garbage_yaml_fails() -> None:
    result = schema_check("this is: not\n  a: valid: profile: [")
    assert result["ok"] is False
    assert any("YAML parse" in e or "missing" in e for e in result["errors"])


def test_top_level_list_fails() -> None:
    result = schema_check("- one\n- two\n")
    assert result["ok"] is False
    assert any("must be a mapping" in e for e in result["errors"])


def test_missing_union_fails() -> None:
    yaml_text = VALID_PROFILE.replace("union: sprinkler_fitters_120\n", "")
    result = schema_check(yaml_text)
    assert result["ok"] is False
    assert any("missing required top-level key: 'union'" in e for e in result["errors"])


def test_missing_constants_key_fails() -> None:
    yaml_text = VALID_PROFILE.replace("  Trade: Sprinkler\n", "")
    result = schema_check(yaml_text)
    assert result["ok"] is False
    assert any("Trade" in e for e in result["errors"])


def test_wrong_key_columns_fails() -> None:
    yaml_text = VALID_PROFILE.replace(
        "key_columns: [Zone, Package, Start Date, End Date]",
        "key_columns: [Zone, Package]",
    )
    result = schema_check(yaml_text)
    assert result["ok"] is False
    assert any("key_columns" in e for e in result["errors"])


def test_columns_missing_leading_block_fails() -> None:
    yaml_text = """\
union: sprinkler_fitters_120
constants:
  Union Group: UA
  Trade: Sprinkler
  Union Local: "120"
start_date: 1/1/26
end_date: 7/31/26
key_columns: [Zone, Package, Start Date, End Date]
columns:
  - {name: Wage, kind: $}
"""
    result = schema_check(yaml_text)
    assert result["ok"] is False


def test_column_invalid_kind_fails() -> None:
    yaml_text = VALID_PROFILE.replace("kind: $}", "kind: dollars}")
    result = schema_check(yaml_text)
    assert result["ok"] is False
    assert any("kind" in e for e in result["errors"])


def test_derived_column_without_factor_fails() -> None:
    bad = VALID_PROFILE.replace(
        "{name: Wage 1.5x, kind: $, multiplier_of: Wage, factor: 1.5}",
        "{name: Wage 1.5x, kind: $, multiplier_of: Wage}",
    )
    result = schema_check(bad)
    assert result["ok"] is False
    assert any("factor" in e for e in result["errors"])


def test_duplicate_column_name_fails() -> None:
    bad = VALID_PROFILE.replace(
        "- {name: Health & Welfare, kind: $}\n",
        "- {name: Health & Welfare, kind: $}\n  - {name: Health & Welfare, kind: $}\n",
    )
    result = schema_check(bad)
    assert result["ok"] is False
    assert any("duplicate" in e for e in result["errors"])
