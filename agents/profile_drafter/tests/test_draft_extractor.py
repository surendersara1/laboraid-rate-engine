"""Tests for draft_extractor.py (E.3) — static contract + mock-mode behaviour."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

import draft_extractor  # noqa: E402


def test_module_has_dual_mode_helpers() -> None:
    assert hasattr(draft_extractor, "_call_bedrock")
    assert hasattr(draft_extractor, "_call_anthropic_direct")


def test_system_prompt_encodes_never_fabricate() -> None:
    src = (_AGENT_DIR / "draft_extractor.py").read_text(encoding="utf-8")
    assert "NEVER FABRICATE" in src
    assert "extract_<local>" in src
    assert "return rows, gaps" in src
    assert "ClassificationRow" in src
    assert "RateCell" in src


def test_strip_fences_handles_python_block() -> None:
    fenced = "```python\ndef extract_120(union_dir):\n    return [], []\n```"
    out = draft_extractor._strip_fences(fenced)
    assert out.strip().startswith("def extract_120")
    assert "```" not in out


def test_no_creds_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", raising=False)
    monkeypatch.delenv("AWS_WEB_IDENTITY_TOKEN_FILE", raising=False)
    monkeypatch.setattr("os.path.exists", lambda _p: False)

    with pytest.raises(RuntimeError, match="No LLM creds"):
        draft_extractor.draft_extractor_python("sprinkler_fitters_120", "union: x\n", "")


def test_anthropic_path_taken_when_api_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    captured: dict[str, object] = {}

    def fake_direct(user_text: str, pdf_bytes: bytes | None) -> str:
        captured["user_text"] = user_text
        captured["pdf_bytes"] = pdf_bytes
        return "def extract_120(union_dir):\n    return [], []\n"

    monkeypatch.setattr(draft_extractor, "_call_anthropic_direct", fake_direct)
    out = draft_extractor.draft_extractor_python("sprinkler_fitters_120", "union: x\n", "")
    assert out.strip().startswith("def extract_120")
    assert "Local number: 120" in str(captured["user_text"])
    assert captured["pdf_bytes"] is None


def test_pdf_attached_when_path_exists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    pdf = tmp_path / "notice.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    captured: dict[str, object] = {}

    def fake_direct(user_text: str, pdf_bytes: bytes | None) -> str:
        captured["pdf_bytes"] = pdf_bytes
        return "def extract_120(union_dir):\n    return [], []\n"

    monkeypatch.setattr(draft_extractor, "_call_anthropic_direct", fake_direct)
    draft_extractor.draft_extractor_python("sprinkler_fitters_120", "union: x\n", str(pdf))
    assert captured["pdf_bytes"] == b"%PDF-1.4 fake"
