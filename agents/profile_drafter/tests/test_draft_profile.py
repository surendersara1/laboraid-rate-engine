"""Tests for draft_profile.py (E.2) — static contract + mock-mode behaviour."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

import draft_profile  # noqa: E402

# ---------------------------------------------------------------------------
# Static source contract
# ---------------------------------------------------------------------------


def test_module_has_dual_mode_helpers() -> None:
    """Both _call_bedrock and _call_anthropic_direct must exist (same pattern as
    extract_generic.py)."""
    assert hasattr(draft_profile, "_call_bedrock")
    assert hasattr(draft_profile, "_call_anthropic_direct")


def test_system_prompt_constrains_output_to_yaml_only() -> None:
    src = (_AGENT_DIR / "draft_profile.py").read_text(encoding="utf-8")
    # Hard constraints in the embedded system prompt.
    assert "no markdown fences" in src.lower()
    assert "NEVER fabricate" in src
    assert "UNKNOWN_FIELDS" in src


def test_strip_fences_handles_yaml_block() -> None:
    fenced = "```yaml\nunion: foo\n```"
    out = draft_profile._strip_fences(fenced)
    assert out.strip() == "union: foo"


def test_strip_fences_handles_unlabeled_block() -> None:
    fenced = "```\nunion: foo\n```"
    out = draft_profile._strip_fences(fenced)
    assert out.strip() == "union: foo"


def test_strip_fences_passes_through_plain_yaml() -> None:
    plain = "union: foo\nconstants: {}\n"
    out = draft_profile._strip_fences(plain)
    assert out.strip() == plain.strip()


# ---------------------------------------------------------------------------
# Mock-mode behaviour: no creds → RuntimeError
# ---------------------------------------------------------------------------


def test_no_creds_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", raising=False)
    monkeypatch.delenv("AWS_WEB_IDENTITY_TOKEN_FILE", raising=False)
    # Pretend no ~/.aws/credentials file exists so _has_aws_creds returns False.
    monkeypatch.setattr("os.path.exists", lambda _p: False)

    with pytest.raises(RuntimeError, match="No LLM creds"):
        draft_profile.draft_profile_yaml("sprinkler_fitters_120", {"columns": []})


def test_anthropic_path_taken_when_api_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    captured: dict[str, object] = {}

    def fake_direct(user_text: str) -> str:
        captured["user_text"] = user_text
        return "union: drafted\n"

    monkeypatch.setattr(draft_profile, "_call_anthropic_direct", fake_direct)
    out = draft_profile.draft_profile_yaml(
        "sprinkler_fitters_120",
        {"columns": [{"name": "Wage", "kind": "$"}]},
        cba_summary="Building zone only.",
    )
    assert out.strip() == "union: drafted"
    assert "Union: sprinkler_fitters_120" in str(captured["user_text"])
    assert "Building zone only." in str(captured["user_text"])


def test_bedrock_path_taken_when_only_aws_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAFAKE")

    captured: dict[str, object] = {}

    def fake_bedrock(user_text: str) -> str:
        captured["user_text"] = user_text
        return "union: bedrock\n"

    monkeypatch.setattr(draft_profile, "_call_bedrock", fake_bedrock)
    out = draft_profile.draft_profile_yaml("sprinkler_fitters_120", {"columns": []})
    assert out.strip() == "union: bedrock"
    assert "user_text" in captured
