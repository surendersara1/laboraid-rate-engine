"""Lightweight tests for the ExtractorAgent SOP (no Strands/kernel imports).

The agent module itself imports `strands` + the kernel, which are only present in
the container, so these tests assert the static contract instead.
"""

from __future__ import annotations

from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent.parent


def test_system_prompt_has_never_fabricate_rule() -> None:
    text = (_AGENT_DIR / "system-prompt.md").read_text(encoding="utf-8")
    assert "never fabricate" in text.lower()
    assert "MUST NOT" in text


def test_agent_imports_kernel_top_level_not_kernel_pkg() -> None:
    # The kernel is a flat package=false project on PYTHONPATH=/opt/kernel, so the
    # agent must import `pipeline`/`canonical`, never `kernel.pipeline`.
    src = (_AGENT_DIR / "agent.py").read_text(encoding="utf-8")
    assert "from pipeline import" in src
    assert "from kernel.pipeline" not in src


def test_dockerfile_installs_kernel_editable() -> None:
    dockerfile = (_AGENT_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert "uv pip install --system -e /opt/kernel" in dockerfile
