"""ProfileDrafterAgent — Strands agent that auto-generates per-union artifacts.

The agent orchestrates 5 tools end-to-end:

    analyze_groundtruth ─► draft_profile_yaml ─► draft_extractor_python
                                                          │
                                                          ▼
                                                validate_generated
                                                          │
                                                          ▼
                                                iterate_or_finalize

Output: a per-union profile YAML + an `extract_<local>` Python function that
match the schema of the existing reference artifacts (704/483/537).

Deployed on AgentCore Runtime via ``BedrockAgentCoreApp``; ``app.run()`` is
called at module import time (not behind ``__main__``) so the container's
import of this module starts the invoke server — same pattern as
ExtractorAgent (audit B7 / decision D-B7).
"""

from __future__ import annotations

import json
import os
from typing import Any

# Strands SDK (installed in the container; untyped third-party).
from strands import Agent, tool  # type: ignore[import-not-found]

# Drafting tool implementations. Each module is independently testable.
from analyze import analyze_groundtruth as _analyze_impl
from codegen_check import codegen_check
from draft_extractor import draft_extractor_python as _draft_extractor_impl
from draft_profile import draft_profile_yaml as _draft_profile_impl
from iterate import iterate_or_finalize as _iterate_impl
from schema_check import schema_check
from steering import DrafterSteering
from validate import validate_generated as _validate_impl


ENV = os.environ.get("ENV", "dev")
INPUTS_BUCKET = os.environ.get("INPUTS_BUCKET", "")
OUTPUTS_BUCKET = os.environ.get("OUTPUTS_BUCKET", "")
PROFILES_DIR = os.environ.get("PROFILES_DIR", "/opt/profiles")
BEDROCK_GUARDRAIL_ID = os.environ.get("BEDROCK_GUARDRAIL_ID", "")
SCRATCH = os.environ.get("AGENT_SCRATCH", "/tmp/agent-runs")

with open(os.path.join(os.path.dirname(__file__), "system-prompt.md"), encoding="utf-8") as _f:
    DRAFTER_SYSTEM_PROMPT = _f.read()


# --- tools (thin wrappers around the dedicated modules) ----------------------
@tool
def analyze_groundtruth(ratesheet_path: str) -> dict[str, Any]:
    """Open the customer's CSV/xlsx, classify columns, sample rows. Pure Python."""
    return _analyze_impl(ratesheet_path)


@tool
def draft_profile_yaml(
    union: str,
    groundtruth_analysis: dict[str, Any],
    cba_summary: str = "",
) -> str:
    """Bedrock Sonnet — emit a profile YAML matching the 704 reference schema."""
    return _draft_profile_impl(union, groundtruth_analysis, cba_summary)


@tool
def draft_extractor_python(
    union: str,
    profile_yaml: str,
    sample_rate_notice_path: str = "",
) -> str:
    """Bedrock Sonnet — emit Python source for ``extract_<local>(union_dir)``."""
    return _draft_extractor_impl(union, profile_yaml, sample_rate_notice_path)


@tool
def validate_generated(
    profile_path_candidate: str,
    extractor_path_candidate: str,
    union_dir: str,
    groundtruth_path: str,
) -> dict[str, Any]:
    """Run schema_check + codegen_check + (if both pass) the kernel evaluator."""
    return _validate_impl(
        profile_path_candidate,
        extractor_path_candidate,
        union_dir,
        groundtruth_path,
    )


@tool
def iterate_or_finalize(
    union: str,
    drafts_so_far: int,
    validation_result: dict[str, Any],
) -> str:
    """Loop control: regenerate_profile | regenerate_extractor | finalize | escalate."""
    return _iterate_impl(union, drafts_so_far, validation_result)


# Re-export helpers so tests can verify they're wired without an LLM call.
__all__ = [
    "analyze_groundtruth",
    "draft_profile_yaml",
    "draft_extractor_python",
    "validate_generated",
    "iterate_or_finalize",
    "build_agent",
    "schema_check",
    "codegen_check",
]


def build_agent() -> Agent:
    """Construct the Strands ProfileDrafterAgent with steering."""
    return Agent(
        name="ProfileDrafterAgent",
        system_prompt=DRAFTER_SYSTEM_PROMPT,
        tools=[
            analyze_groundtruth,
            draft_profile_yaml,
            draft_extractor_python,
            validate_generated,
            iterate_or_finalize,
        ],
        plugins=[DrafterSteering()],
        trace_attributes={"service": "laboraid-profile-drafter", "env": ENV},
    )


# --- AgentCore Runtime entrypoint -------------------------------------------
try:  # pragma: no cover - only present in the deployed container
    from bedrock_agentcore.runtime import (  # type: ignore[import-not-found]
        BedrockAgentCoreApp,
    )

    app = BedrockAgentCoreApp()

    @app.entrypoint  # type: ignore[misc]
    def invoke(payload: dict[str, Any]) -> Any:
        """AgentCore Runtime entrypoint — payload carries union + paths."""
        agent = build_agent()
        return agent(payload.get("prompt", json.dumps(payload)))

    # Run unconditionally when the AgentCore SDK is importable. AgentCore loads
    # this module on container start (it does NOT run it as __main__), so the
    # server MUST start here — gating on `__name__ == "__main__"` would leave
    # the entrypoint registered but never listening (audit B7 / decision D-B7).
    app.run()
except ImportError:  # pragma: no cover - local dev / unit tests without AgentCore SDK
    # The Strands @tool functions and build_agent() remain importable so unit
    # tests can exercise the agent logic without the AgentCore runtime.
    pass
