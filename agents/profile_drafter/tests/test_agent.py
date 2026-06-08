"""Static contract tests for agent.py — verify the 5 @tool fns + steering.

We don't construct the live Agent (that wants Bedrock credentials at runtime).
Instead we read agent.py as text and import the module-level objects to
confirm the wiring is right.
"""

from __future__ import annotations

import sys
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))


def test_agent_source_lists_all_five_tools() -> None:
    src = (_AGENT_DIR / "agent.py").read_text(encoding="utf-8")
    for tool in (
        "analyze_groundtruth",
        "draft_profile_yaml",
        "draft_extractor_python",
        "validate_generated",
        "iterate_or_finalize",
    ):
        assert f"def {tool}(" in src, f"agent.py must declare a {tool} @tool wrapper"


def test_agent_source_decorates_with_tool() -> None:
    src = (_AGENT_DIR / "agent.py").read_text(encoding="utf-8")
    # 5 @tool decorators at column 0, one per wrapper (filter out commentary
    # mentions like '# The Strands @tool functions...').
    decorator_count = sum(1 for ln in src.splitlines() if ln.strip().startswith("@tool"))
    assert decorator_count == 5, f"expected exactly 5 @tool decorators, got {decorator_count}"


def test_agent_source_registers_drafter_steering_plugin() -> None:
    src = (_AGENT_DIR / "agent.py").read_text(encoding="utf-8")
    assert "DrafterSteering" in src
    assert "plugins=[DrafterSteering()]" in src


def test_agent_app_run_called_unconditionally_inside_try() -> None:
    """Audit B7: app.run() must NOT be gated on __name__ == '__main__'."""
    src = (_AGENT_DIR / "agent.py").read_text(encoding="utf-8")
    assert "app.run()" in src
    # Make sure there's NO __main__ guard around the AgentCore startup. The
    # local-dev fallback is `except ImportError: pass`, not a __main__ block.
    assert 'if __name__ == "__main__":' not in src


def test_agent_imports_kernel_top_level_not_kernel_pkg() -> None:
    src = (_AGENT_DIR / "agent.py").read_text(encoding="utf-8")
    # The drafter container puts the kernel on PYTHONPATH=/opt/kernel — same
    # convention as the extractor — so any kernel import must use the top-level
    # `pipeline` / `canonical` modules. Currently the drafter doesn't directly
    # import the kernel (the tools delegate), but if that changes the
    # convention must hold.
    assert "from kernel.pipeline" not in src
    assert "from kernel.canonical" not in src


def test_agent_source_references_all_tool_modules() -> None:
    """agent.py must wire each @tool to its underlying module function."""
    src = (_AGENT_DIR / "agent.py").read_text(encoding="utf-8")
    # Each tool's @tool wrapper delegates to a module-level impl.
    assert "_analyze_impl" in src or "from analyze import" in src
    assert "_draft_profile_impl" in src or "from draft_profile import" in src
    assert "_draft_extractor_impl" in src or "from draft_extractor import" in src
    assert "_validate_impl" in src or "from validate import" in src
    assert "_iterate_impl" in src or "from iterate import" in src


def test_agent_source_imports_steering_and_check_helpers() -> None:
    """The agent module re-exports the schema/codegen checks for tooling use."""
    src = (_AGENT_DIR / "agent.py").read_text(encoding="utf-8")
    assert "from steering import DrafterSteering" in src
    assert "from schema_check import schema_check" in src
    assert "from codegen_check import codegen_check" in src


def test_agent_build_agent_function_present() -> None:
    """build_agent() must be defined as a top-level function in agent.py.

    We do NOT import agent.py directly because importing triggers
    BedrockAgentCoreApp.app.run() (audit B7) which binds to :8080 and
    blocks the test process. The static source check is sufficient — the
    individual tool modules are already covered by their own tests.
    """
    src = (_AGENT_DIR / "agent.py").read_text(encoding="utf-8")
    assert "def build_agent()" in src
    assert "Agent(" in src
    assert 'name="ProfileDrafterAgent"' in src
    # The 5 wrappers must appear inside the tools=[...] list passed to Agent.
    tools_block_idx = src.find("tools=[")
    assert tools_block_idx > 0, "tools=[...] block not found"
    tail = src[tools_block_idx:]
    for tool in (
        "analyze_groundtruth",
        "draft_profile_yaml",
        "draft_extractor_python",
        "validate_generated",
        "iterate_or_finalize",
    ):
        assert tool in tail[:600], f"{tool} not in the Agent(tools=[...]) list"
