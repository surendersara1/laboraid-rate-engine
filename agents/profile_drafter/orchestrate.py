"""F.1 — orchestrate: the testable Python entry for the ProfileDrafterAgent.

Runs the full drafting chain end-to-end via direct function calls (not via
Strands @tool dispatch — that's the agent.py invocation path). The orchestrator
is what unit tests and external callers (e.g. process_customer_samples.py)
invoke; the @tool methods in agent.py wrap the same underlying functions for
the AgentCore-Runtime path.

Loop shape:

    iteration 0:
        analyze_groundtruth(ratesheet)
        draft_profile_yaml(union, analysis, cba_summary)
        draft_extractor_python(union, profile_yaml, rate_notice_path)
        validate_generated(...)
        iterate_or_finalize(...)  → finalize | regenerate_* | escalate

    iteration k (if regenerate_*):
        re-run the matching draft step + validate + decide again

Returns:

    {
      "profile_yaml": str,
      "extractor_py": str,
      "validation": dict,             # last validation result
      "iterations": int,              # 1-indexed total iterations attempted
      "status": "drafted" | "escalated",
    }

Hard guarantees:
* Will not exceed ``max_iterations`` total iterations.
* Will not call any LLM helper when no AWS / Anthropic creds are wired up
  (the helpers themselves raise; orchestrate propagates the error).
* Writes candidate artifacts to a scratch directory (default
  ``$AGENT_SCRATCH/<union>/candidates/``); does NOT mutate kernel/ on disk.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from analyze import analyze_groundtruth
from draft_extractor import draft_extractor_python
from draft_profile import draft_profile_yaml
from iterate import (
    DEFAULT_ACCURACY_THRESHOLD,
    DEFAULT_MAX_ITERATIONS,
    iterate_or_finalize,
)
from validate import validate_generated


def orchestrate(
    union_key: str,
    cba_dir: str,
    ratesheet_path: str,
    accuracy_threshold: float = DEFAULT_ACCURACY_THRESHOLD,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    cba_summary: str = "",
    scratch_root: str | None = None,
) -> dict[str, Any]:
    """End-to-end orchestrator. See module docstring for inputs/outputs.

    Args:
        union_key: kernel union key, e.g. ``"sprinkler_fitters_120"``.
        cba_dir: path to ``data/<union>/`` (Rate Notice PDFs in ``cba/``).
        ratesheet_path: the customer's existing ratesheet (CSV or xlsx).
        accuracy_threshold: minimum accuracy_pct to finalize; default 70.0.
        max_iterations: hard ceiling on draft attempts; default 3.
        cba_summary: optional structural CBA notes for the profile prompt.
        scratch_root: where to write candidate artifacts; defaults to
            ``$AGENT_SCRATCH/<union>/candidates`` or a tempdir.
    """
    scratch = _make_scratch(scratch_root, union_key)

    analysis = analyze_groundtruth(ratesheet_path)
    rate_notice = _find_rate_notice(cba_dir)

    profile_yaml = draft_profile_yaml(union_key, analysis, cba_summary=cba_summary)
    extractor_py = draft_extractor_python(
        union_key, profile_yaml, sample_rate_notice_path=rate_notice
    )

    profile_path = scratch / f"{union_key}.yaml"
    extractor_path = scratch / f"extract_{_local(union_key)}.py"
    profile_path.write_text(profile_yaml, encoding="utf-8")
    extractor_path.write_text(extractor_py, encoding="utf-8")

    iterations = 1
    validation = validate_generated(
        str(profile_path), str(extractor_path), cba_dir, ratesheet_path
    )
    action = iterate_or_finalize(
        union_key,
        iterations,
        validation,
        accuracy_threshold=accuracy_threshold,
        max_iterations=max_iterations,
    )

    while action in ("regenerate_profile", "regenerate_extractor"):
        iterations += 1
        if iterations > max_iterations:
            action = "escalate"
            break

        if action == "regenerate_profile":
            profile_yaml = draft_profile_yaml(
                union_key, analysis, cba_summary=cba_summary
            )
            profile_path.write_text(profile_yaml, encoding="utf-8")
        else:  # regenerate_extractor
            extractor_py = draft_extractor_python(
                union_key, profile_yaml, sample_rate_notice_path=rate_notice
            )
            extractor_path.write_text(extractor_py, encoding="utf-8")

        validation = validate_generated(
            str(profile_path), str(extractor_path), cba_dir, ratesheet_path
        )
        action = iterate_or_finalize(
            union_key,
            iterations,
            validation,
            accuracy_threshold=accuracy_threshold,
            max_iterations=max_iterations,
        )

    status = "drafted" if action == "finalize" else "escalated"
    return {
        "profile_yaml": profile_yaml,
        "extractor_py": extractor_py,
        "validation": validation,
        "iterations": iterations,
        "status": status,
    }


def _make_scratch(scratch_root: str | None, union_key: str) -> Path:
    """Resolve and create the candidate scratch directory."""
    if scratch_root:
        base = Path(scratch_root)
    else:
        env_scratch = os.environ.get("AGENT_SCRATCH")
        base = Path(env_scratch) if env_scratch else Path(tempfile.gettempdir())
    out = base / union_key / "candidates"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _find_rate_notice(cba_dir: str) -> str:
    """Best-effort: find a 'Rate Notice' PDF under cba_dir/cba/. Empty if none."""
    p = Path(cba_dir) / "cba"
    if not p.exists():
        return ""
    candidates = sorted(
        pdf for pdf in p.rglob("*.pdf") if _looks_like_notice(pdf.name)
    )
    return str(candidates[-1]) if candidates else ""


def _looks_like_notice(name: str) -> bool:
    n = name.lower()
    return any(
        kw in n for kw in ("rate notice", "wage notice", "wage rate notice", "wage sheet")
    )


def _local(union_key: str) -> str:
    """Extract the trailing digits-local from a union key like ``sprinkler_fitters_120``."""
    if "_" not in union_key:
        return union_key
    return union_key.rsplit("_", 1)[-1]
