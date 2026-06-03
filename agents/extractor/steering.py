"""ExtractorAgent steering policy (Spec/09 §5.3).

`ExtractorSteering` enforces the self-validation contract: the agent may not
declare the extraction complete until the Total Package checksum has been
validated, and it must attempt the Bedrock multi-modal fallback for any kernel
gaps before finishing.
"""

from __future__ import annotations

from typing import Any

from strands.vended_plugins.steering import (  # type: ignore[import-not-found]
    Guide,
    Proceed,
    SteeringHandler,
)


class ExtractorSteering(SteeringHandler):  # type: ignore[misc]
    """Block premature completion; force checksum + gap-escalation discipline."""

    async def steer_before_tool(
        self, *, agent: Any, tool_use: dict[str, Any], **kwargs: Any
    ) -> Any:
        if tool_use["name"] == "return_extraction_complete":
            if not getattr(agent, "checksum_validated", False):
                return Guide(reason="Run validate_total_package_checksum first.")
            unresolved = getattr(agent, "unresolved_gaps", [])
            if unresolved and not getattr(agent, "bedrock_fallback_attempted", False):
                return Guide(
                    reason=(
                        f"Kernel reported {len(unresolved)} gaps. Try "
                        "escalate_to_claude_multimodal for these fields before "
                        f"declaring done: {unresolved}"
                    )
                )
        return Proceed(reason="OK.")
