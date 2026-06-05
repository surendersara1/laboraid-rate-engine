"""E.5 — iterate_or_finalize: loop control for the drafting agent.

Decides what to do next given the most recent validation result:

* schema_check failed → regenerate the profile YAML.
* codegen_check failed → regenerate the extractor Python.
* both passed but accuracy < threshold → regenerate the extractor (the
  profile structure was OK; the Python's value-extraction logic is the
  thing that's off).
* both passed AND accuracy >= threshold → finalize.
* exceeded max_iterations without success → escalate.

Uses a pure-Python heuristic (deterministic, free, and offline-testable). The
spec calls for a small Bedrock Haiku call, but the heuristic encodes the
same decision tree the prompt would emit and avoids extra LLM cost. If a
real Haiku call is desired in the future, swap in the same dual-mode helper
pattern used in draft_profile.py / draft_extractor.py.

Returns one of: ``"regenerate_profile"``, ``"regenerate_extractor"``,
``"finalize"``, ``"escalate"``.
"""

from __future__ import annotations

from typing import Any

DEFAULT_ACCURACY_THRESHOLD: float = 70.0
DEFAULT_MAX_ITERATIONS: int = 3


def iterate_or_finalize(
    union: str,
    drafts_so_far: int,
    validation_result: dict[str, Any],
    accuracy_threshold: float = DEFAULT_ACCURACY_THRESHOLD,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> str:
    """Decide the next action. See module docstring for the decision tree.

    Args:
        union: kernel union key (informational only).
        drafts_so_far: how many full draft cycles have been attempted.
        validation_result: dict from validate.validate_generated.
        accuracy_threshold: percent; default 70.0.
        max_iterations: hard ceiling on drafting attempts; default 3.

    Returns:
        One of the four action strings listed above.
    """
    del union  # informational; reserved for richer future heuristics

    schema_pass = bool(validation_result.get("schema_pass", False))
    codegen_pass = bool(validation_result.get("codegen_pass", False))
    accuracy = float(validation_result.get("accuracy_pct", 0.0))

    # Hit the ceiling?  Escalate regardless of how close we got.
    if drafts_so_far >= max_iterations:
        return "escalate"

    if not schema_pass:
        return "regenerate_profile"
    if not codegen_pass:
        return "regenerate_extractor"
    if accuracy < accuracy_threshold:
        return "regenerate_extractor"
    return "finalize"
