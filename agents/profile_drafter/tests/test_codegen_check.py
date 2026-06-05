"""Tests for codegen_check.py (D.4)."""

from __future__ import annotations

import sys
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from codegen_check import codegen_check  # noqa: E402

VALID_EXTRACTOR = '''\
"""extract_120 — minimal happy-path extractor stub."""
from __future__ import annotations


def extract_120(union_dir):
    rows = []
    gaps = []
    # ... actual parsing would go here ...
    return rows, gaps
'''


def test_valid_extractor_passes() -> None:
    result = codegen_check(VALID_EXTRACTOR)
    assert result["ok"] is True, f"errors: {result['errors']}"
    assert result["syntax_pass"] is True
    assert result["signature_pass"] is True
    assert result["returns_tuple_pass"] is True
    assert result["function_name"] == "extract_120"


def test_valid_extractor_with_expected_local() -> None:
    result = codegen_check(VALID_EXTRACTOR, expected_local="120")
    assert result["ok"] is True
    assert result["function_name"] == "extract_120"


def test_wrong_local_fails() -> None:
    result = codegen_check(VALID_EXTRACTOR, expected_local="999")
    assert result["ok"] is False
    assert any("extract_999" in e for e in result["errors"])


def test_syntax_error_caught() -> None:
    bad = "def extract_120(union_dir):\n    return rows gaps\n"  # missing comma
    result = codegen_check(bad)
    assert result["ok"] is False
    assert result["syntax_pass"] is False
    assert any("py_compile" in e or "ast.parse" in e for e in result["errors"])


def test_missing_extract_function_fails() -> None:
    bad = "def helper(x):\n    return 1, 2\n"
    result = codegen_check(bad)
    assert result["ok"] is False
    assert any("extract_" in e for e in result["errors"])


def test_wrong_arg_name_fails() -> None:
    bad = "def extract_120(path):\n    return [], []\n"
    result = codegen_check(bad)
    assert result["ok"] is False
    assert result["signature_pass"] is False
    assert any("union_dir" in e for e in result["errors"])


def test_too_many_args_fails() -> None:
    bad = "def extract_120(union_dir, extra):\n    return [], []\n"
    result = codegen_check(bad)
    assert result["ok"] is False
    assert result["signature_pass"] is False


def test_varargs_rejected() -> None:
    bad = "def extract_120(*args):\n    return [], []\n"
    result = codegen_check(bad)
    assert result["ok"] is False
    assert any("*args" in e for e in result["errors"])


def test_kwargs_rejected() -> None:
    bad = "def extract_120(union_dir, **kw):\n    return [], []\n"
    result = codegen_check(bad)
    assert result["ok"] is False
    assert any("**kwargs" in e for e in result["errors"])


def test_bare_return_fails() -> None:
    bad = "def extract_120(union_dir):\n    return\n"
    result = codegen_check(bad)
    assert result["ok"] is False
    assert result["returns_tuple_pass"] is False


def test_single_value_return_fails() -> None:
    bad = "def extract_120(union_dir):\n    return []\n"
    result = codegen_check(bad)
    assert result["ok"] is False
    assert result["returns_tuple_pass"] is False


def test_three_tuple_return_fails() -> None:
    bad = "def extract_120(union_dir):\n    return [], [], []\n"
    result = codegen_check(bad)
    assert result["ok"] is False
    assert result["returns_tuple_pass"] is False


def test_parenthesized_tuple_return_accepted() -> None:
    good = "def extract_120(union_dir):\n    return ([], [])\n"
    result = codegen_check(good)
    assert result["ok"] is True


def test_multiple_returns_all_must_be_2tuples() -> None:
    mixed = (
        "def extract_120(union_dir):\n"
        "    if not union_dir:\n"
        "        return [], []\n"
        "    return []\n"  # bad
    )
    result = codegen_check(mixed)
    assert result["ok"] is False
    assert result["returns_tuple_pass"] is False
