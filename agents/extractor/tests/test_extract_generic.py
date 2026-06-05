"""Tests for Path C — generic Claude-based extractor.

Follows the same source-as-text approach as `test_system_prompt.py`: the
extract_generic module imports the kernel via PYTHONPATH=/opt/kernel which only
exists in the container, so we assert the static contract.

Where pure Python logic can be exercised in isolation (JSON parsing helpers,
filename heuristics), we load the module via importlib with `canonical` stubbed
out so the import succeeds locally.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Static source contract — same approach as test_system_prompt.py
# ---------------------------------------------------------------------------

def test_extract_generic_exposes_path_c_entry() -> None:
    src = (_AGENT_DIR / "extract_generic.py").read_text(encoding="utf-8")
    assert "def extract_via_claude(" in src, "missing Path C entry point"
    assert "ClassificationRow" in src, "should return canonical ClassificationRow objects"
    assert "RateCell" in src, "cells should be RateCell instances with provenance"


def test_extract_generic_dual_mode_bedrock_or_anthropic() -> None:
    """Production = Bedrock, local dev = Anthropic direct (per module docstring)."""
    src = (_AGENT_DIR / "extract_generic.py").read_text(encoding="utf-8")
    assert "bedrock-runtime" in src, "missing Bedrock production path"
    assert "ANTHROPIC_API_KEY" in src, "missing local-dev Anthropic-direct path"
    assert "_call_bedrock" in src
    assert "_call_anthropic_direct" in src


def test_extract_generic_enforces_never_fabricate() -> None:
    """System prompt embedded in the module must enforce the project rule."""
    src = (_AGENT_DIR / "extract_generic.py").read_text(encoding="utf-8")
    # Look in the SYSTEM PROMPT block.
    assert "NEVER FABRICATE" in src, "Path C must carry the never-fabricate directive"
    assert "set its value to null" in src, "must instruct null on missing cells"


def test_agent_registers_path_c_tool() -> None:
    """The Strands agent must register extract_via_claude_only in its tools list."""
    src = (_AGENT_DIR / "agent.py").read_text(encoding="utf-8")
    assert "from extract_generic import extract_via_claude" in src
    assert "@tool" in src
    assert "def extract_via_claude_only(" in src
    # In build_agent() tools=[...] block:
    assert "extract_via_claude_only," in src, "tool must be in the Agent(tools=[...]) list"


def test_system_prompt_explains_path_a_b_c() -> None:
    """SOP must call out all 3 extraction paths so the LLM picks the right one."""
    text = (_AGENT_DIR / "system-prompt.md").read_text(encoding="utf-8")
    assert "Path A" in text, "missing Path A label in tool descriptions"
    assert "Path B" in text, "missing Path B label"
    assert "Path C" in text, "missing Path C label"
    assert "extract_via_claude_only" in text, "Path C tool must be named in SOP"
    assert "Path C unions skip" in text, "must instruct skipping compute_derived_columns on Path C"


# ---------------------------------------------------------------------------
# Pure-function unit tests — load the module with `canonical` stubbed.
# ---------------------------------------------------------------------------

def _load_module_with_canonical_stub() -> types.ModuleType:
    """Inject a minimal `canonical.model` stub so extract_generic can import."""
    canonical_pkg = types.ModuleType("canonical")
    model_mod = types.ModuleType("canonical.model")

    class _ClassificationRow:
        def __init__(self, zone: str, classification: str, class_order: int) -> None:
            self.zone = zone
            self.classification = classification
            self.class_order = class_order
            self.cells: dict = {}

        def add(self, cell: object) -> None:
            cf = getattr(cell, "canonical_field", "")
            self.cells[cf] = cell

    class _RateCell:
        def __init__(self, **kwargs: object) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    model_mod.ClassificationRow = _ClassificationRow  # type: ignore[attr-defined]
    model_mod.RateCell = _RateCell  # type: ignore[attr-defined]
    sys.modules["canonical"] = canonical_pkg
    sys.modules["canonical.model"] = model_mod

    # Make sure the agent dir is on sys.path for the import.
    if str(_AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(_AGENT_DIR))

    if "extract_generic" in sys.modules:
        del sys.modules["extract_generic"]
    return importlib.import_module("extract_generic")


def test_looks_like_notice_heuristic() -> None:
    m = _load_module_with_canonical_stub()
    assert m._looks_like_notice("2026.01.01.704 Rate Notice.pdf")
    assert m._looks_like_notice("2024.01.01.483 Wage Rate Notice.pdf")
    assert m._looks_like_notice("2025.07.01.696 Building Trades Wage Rates.pdf")
    assert m._looks_like_notice("2025.01.01.696 Wage Sheet.pdf")
    assert not m._looks_like_notice("2022.08.01-2027.07.31.704 CBA.pdf")
    assert not m._looks_like_notice("2024-2027.183 Articles.xlsx")


def test_extract_json_object_strips_markdown_fences() -> None:
    m = _load_module_with_canonical_stub()
    fenced = '```json\n{"rows": [{"zone": "Building"}]}\n```'
    out = m._extract_json_object(fenced)
    assert out == {"rows": [{"zone": "Building"}]}


def test_extract_json_object_recovers_from_prose_wrap() -> None:
    m = _load_module_with_canonical_stub()
    noisy = 'Sure, here is the JSON: {"rows": []}  and that is it.'
    out = m._extract_json_object(noisy)
    assert out == {"rows": []}


def test_extract_json_object_returns_empty_on_garbage() -> None:
    m = _load_module_with_canonical_stub()
    assert m._extract_json_object("not json at all") == {"rows": []}
    assert m._extract_json_object("") == {"rows": []}


def test_value_kind_classification() -> None:
    m = _load_module_with_canonical_stub()
    assert m._value_kind(54.70) == "$"
    assert m._value_kind(12) == "$"
    assert m._value_kind("6.00%") == "%"
    assert m._value_kind("UA") == "raw"


def test_column_to_canonical_kebab_to_snake() -> None:
    m = _load_module_with_canonical_stub()
    assert m._column_to_canonical("Wage") == "wage"
    assert m._column_to_canonical("Health & Welfare") == "health_welfare"
    assert m._column_to_canonical("S & E 704") == "s_e_704"
    assert m._column_to_canonical("UA International Training") == "ua_international_training"


def test_parse_response_builds_rows_with_provenance() -> None:
    m = _load_module_with_canonical_stub()
    response = {
        "rows": [
            {
                "zone": "Building",
                "classification": "Journeyman",
                "class_order": 90,
                "cells": {
                    "Wage": {"value": 54.70, "source_locator": "p2/t1/r3", "confidence": 0.95},
                    "Health & Welfare": {"value": 12.50, "source_locator": "p2/t1/r3", "confidence": 0.9},
                    "S & E 704": {"value": None, "source_locator": "not in this notice", "confidence": 0.0},
                },
            }
        ]
    }
    rows, gaps = m._parse_response(response, source_doc="Rate Notice.pdf")
    assert len(rows) == 1
    row = rows[0]
    assert row.zone == "Building"
    assert row.classification == "Journeyman"
    assert row.class_order == 90
    # Two cells stored under canonical keys; the null one is a gap.
    assert "wage" in row.cells
    assert "health_welfare" in row.cells
    assert "s_e_704" not in row.cells
    assert len(gaps) == 1
    assert gaps[0][0] == "Building"
    assert gaps[0][1] == "Journeyman"
    assert gaps[0][2] == "S & E 704"
    # RateCell carries source_doc + locator + confidence (provenance preserved).
    wage_cell = row.cells["wage"]
    assert wage_cell.value == 54.70
    assert wage_cell.source_doc == "Rate Notice.pdf"
    assert wage_cell.source_locator == "p2/t1/r3"
    assert wage_cell.confidence == 0.95


def test_parse_response_handles_empty_input() -> None:
    m = _load_module_with_canonical_stub()
    rows, gaps = m._parse_response({}, source_doc="x.pdf")
    assert rows == []
    assert gaps == []
