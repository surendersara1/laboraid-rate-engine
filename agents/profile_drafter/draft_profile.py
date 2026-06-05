"""E.2 — draft_profile_yaml: Bedrock Sonnet emits a per-union profile YAML.

Wraps a Bedrock Sonnet 4.6 call using the SAME dual-mode pattern as
``agents/extractor/extract_generic.py``:

* ANTHROPIC_API_KEY in the env → ``anthropic`` SDK direct
* otherwise (and AWS creds present) → ``bedrock-runtime`` InvokeModel
* neither → clear ``RuntimeError("No LLM creds — cannot draft")``

The system prompt instructs Claude to output YAML matching the schema of
``kernel/profiles/sprinkler_fitters_704.yaml``, using canonical names from
``kernel/canonical/fields.yaml`` and emitting any non-canonical labels in a
trailing ``# UNKNOWN_FIELDS:`` block.

Output is plain YAML — no markdown fences, no prose.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any


_MODEL_ID = "us.anthropic.claude-sonnet-4-6-v1:0"
_ANTHROPIC_MODEL = "claude-sonnet-4-6-20250930"

_SYSTEM_PROMPT = """You are drafting a per-union profile YAML for the LaborAid Rate Engine.

OUTPUT: only the YAML document, no prose, no markdown fences. Start at column 1
with the `union:` key.

SCHEMA (match the reference profile exactly):

    union: <lower_snake_case union key>
    constants:
      Union Group: <e.g., UA | SMART | UBC>
      Trade: <e.g., Sprinkler | Pipefitter>
      Union Local: "<numeric local, quoted>"
    start_date: <M/D/YY>
    end_date: <M/D/YY>
    key_columns: [Zone, Package, Start Date, End Date]
    columns:
      - Union Group
      - Trade
      - Union Local
      - Zone
      - Package
      - Start Date
      - End Date
      - {name: Wage, kind: $}
      - {name: Wage Differential, kind: $, multiplier_of: Wage, factor: 1.15}
      - ...
      - {name: <fringe column>, kind: $}

RULES:
1. The first 7 entries under `columns:` MUST be the 7 plain strings shown
   above (the key-column echo block), in that exact order.
2. Every other column MUST be a dict with at minimum `name` and `kind`.
   `kind` is one of: $ | % | raw.
3. Derived columns (e.g. Wage 1.5x) carry `multiplier_of: <base column>`
   and a numeric `factor:`. The base column must already appear above.
4. Use canonical field-NAMES from the provided fields.yaml when picking the
   `name:`. The output `name` is the customer's exact column label (e.g.
   "S & E 704", "Health & Welfare").
5. For unknown fields (columns the customer uses that have no entry in
   fields.yaml), still emit them as `{name, kind}` entries in the columns
   list, AND list them after the YAML document in a single trailing
   comment block:
       # UNKNOWN_FIELDS:
       # - <column-name-1>
       # - <column-name-2>
6. NEVER fabricate values. The profile defines column structure only — no
   per-cell amounts go here.
"""


def draft_profile_yaml(
    union: str,
    groundtruth_analysis: dict[str, Any],
    cba_summary: str = "",
) -> str:
    """Call Claude Sonnet to produce a profile YAML; return YAML string only.

    Args:
        union: kernel union key, e.g. ``"sprinkler_fitters_120"``.
        groundtruth_analysis: the dict from ``analyze.analyze_groundtruth``.
        cba_summary: optional human-summarized CBA structural notes (zones,
            classifications, derived-column rules). May be empty.

    Returns:
        YAML document as a string (any leading/trailing markdown fences are
        stripped automatically).
    """
    user_text = _build_user_prompt(union, groundtruth_analysis, cba_summary)

    if os.environ.get("ANTHROPIC_API_KEY"):
        raw = _call_anthropic_direct(user_text)
    elif _has_aws_creds():
        raw = _call_bedrock(user_text)
    else:
        raise RuntimeError("No LLM creds — cannot draft")

    return _strip_fences(raw)


def _has_aws_creds() -> bool:
    """Lightweight check: do we appear to have any AWS creds wired up?"""
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        return True
    if os.environ.get("AWS_PROFILE"):
        return True
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        # Lambda / AgentCore Runtime always carries an execution role.
        return True
    if os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"):
        return True
    if os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE"):
        return True
    if os.path.exists(os.path.expanduser("~/.aws/credentials")):
        return True
    return False


def _build_user_prompt(
    union: str,
    analysis: dict[str, Any],
    cba_summary: str,
) -> str:
    """Render the analysis into a compact user message for Claude."""
    parts: list[str] = []
    parts.append(f"Union: {union}")
    if cba_summary:
        parts.append("\nCBA structural summary:")
        parts.append(cba_summary.strip())
    parts.append("\nGroundtruth analysis:")
    parts.append(json.dumps(analysis, indent=2, default=str))
    parts.append(
        "\nProduce the profile YAML now. Preserve the exact column labels from "
        "the analysis (case + spaces + suffixes such as '704' / '483')."
    )
    return "\n".join(parts)


def _call_bedrock(user_text: str) -> str:
    """Production path — Bedrock Runtime InvokeModel (Sonnet 4.6)."""
    import boto3  # type: ignore[import-untyped]

    client = boto3.client("bedrock-runtime")
    body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4000,
        "system": _SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": user_text}],
            }
        ],
    }
    kwargs: dict[str, Any] = {"modelId": _MODEL_ID, "body": json.dumps(body)}
    guardrail_id = os.environ.get("BEDROCK_GUARDRAIL_ID")
    if guardrail_id:
        kwargs["guardrailIdentifier"] = guardrail_id
        kwargs["guardrailVersion"] = "DRAFT"
    response = client.invoke_model(**kwargs)
    payload = json.loads(response["body"].read())
    content = payload.get("content", [{}])
    if content and isinstance(content, list):
        first = content[0]
        if isinstance(first, dict):
            return str(first.get("text", ""))
    return ""


def _call_anthropic_direct(user_text: str) -> str:
    """Local dev path — direct Anthropic API. Requires ANTHROPIC_API_KEY."""
    import anthropic  # type: ignore[import-untyped]

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=4000,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [{"type": "text", "text": user_text}],
            }
        ],
    )
    if not response.content:
        return ""
    first = response.content[0]
    text = getattr(first, "text", "")
    return str(text)


def _strip_fences(text: str) -> str:
    """Remove ```yaml / ``` fences if Claude wrapped despite instructions."""
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:ya?ml)?\s*\n?", "", stripped)
        stripped = re.sub(r"\n?```\s*$", "", stripped)
    return stripped.strip() + "\n"
