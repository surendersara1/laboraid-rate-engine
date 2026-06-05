"""ProfileDrafterAgent steering policy (Spec/09 §15.1).

`DrafterSteering` enforces the build-time contract: the drafter agent may not
declare a draft complete until `validate_generated` reports

    schema_pass == True
    codegen_pass == True
    accuracy_pct >= configured threshold

If validation has not yet been run, or the most recent validation failed any of
those checks, the agent is steered back to either regenerate or escalate.
"""

from __future__ import annotations

from typing import Any

from strands.types.tools import ToolUse  # type: ignore[import-not-found]
from strands.vended_plugins.steering import (  # type: ignore[import-not-found]
    Guide,
    Proceed,
    SteeringHandler,
)

# Default accuracy threshold (percent). Orchestrator can override on the agent
# via `agent.accuracy_threshold = <float>` before invocation.
DEFAULT_ACCURACY_THRESHOLD: float = 70.0


class DrafterSteering(SteeringHandler):  # type: ignore[misc]
    """Block premature completion; force schema+codegen+accuracy discipline."""

    async def steer_before_tool(self, *, agent: Any, tool_use: ToolUse, **kwargs: Any) -> Any:
        if tool_use["name"] != "return_drafting_complete":
            return Proceed(reason="OK.")

        last_validation: dict[str, Any] | None = getattr(agent, "last_validation", None)
        if last_validation is None:
            return Guide(
                reason=(
                    "Run validate_generated on the candidate profile + extractor "
                    "before declaring done."
                )
            )

        threshold = float(getattr(agent, "accuracy_threshold", DEFAULT_ACCURACY_THRESHOLD))

        failures: list[str] = []
        if not last_validation.get("schema_pass", False):
            failures.append("schema_pass=False — fix the profile YAML structure")
        if not last_validation.get("codegen_pass", False):
            failures.append("codegen_pass=False — fix the extractor Python (syntax / signature)")
        accuracy = float(last_validation.get("accuracy_pct", 0.0))
        if accuracy < threshold:
            failures.append(
                f"accuracy_pct={accuracy:.1f} < threshold={threshold:.1f} — "
                "regenerate or iterate"
            )

        if failures:
            return Guide(reason="; ".join(failures))
        return Proceed(reason="All gates passed.")
