"""Lightweight contract tests for the ProfileDrafterAgent SOP.

Same shape as agents/extractor/tests/test_system_prompt.py — asserts the
static contract without importing the kernel.
"""

from __future__ import annotations

from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent.parent


def test_system_prompt_has_never_fabricate_rule() -> None:
    text = (_AGENT_DIR / "system-prompt.md").read_text(encoding="utf-8")
    assert "never fabricate" in text.lower()
    assert "MUST NOT" in text


def test_system_prompt_lists_all_five_tools() -> None:
    text = (_AGENT_DIR / "system-prompt.md").read_text(encoding="utf-8")
    for tool in (
        "analyze_groundtruth",
        "draft_profile_yaml",
        "draft_extractor_python",
        "validate_generated",
        "iterate_or_finalize",
    ):
        assert tool in text, f"SOP must document the {tool!r} tool"


def test_system_prompt_documents_validation_gates() -> None:
    text = (_AGENT_DIR / "system-prompt.md").read_text(encoding="utf-8")
    assert "schema_pass" in text
    assert "codegen_pass" in text
    assert "accuracy" in text.lower()
    assert "escalate" in text.lower()


def test_dockerfile_installs_kernel_editable() -> None:
    dockerfile = (_AGENT_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert "uv pip install --system -e /opt/kernel" in dockerfile


def test_dockerfile_uses_arm64_python_312() -> None:
    dockerfile = (_AGENT_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert "--platform=linux/arm64" in dockerfile
    assert "python:3.12" in dockerfile


def test_pyproject_lists_required_dependencies() -> None:
    text = (_AGENT_DIR / "pyproject.toml").read_text(encoding="utf-8")
    for dep in (
        "strands-agents",
        "bedrock-agentcore",
        "boto3",
        "anthropic",
        "openpyxl",
        "pyyaml",
        "jsii",
    ):
        assert dep in text, f"pyproject.toml must declare {dep!r}"
