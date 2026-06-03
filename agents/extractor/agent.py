"""ExtractorAgent — Strands agent wrapping the deterministic kernel (Spec/09 §5.3).

The agent's value-add is orchestration + steering + Bedrock fallback; the actual
PDF-to-numbers work is the kernel's. The container puts the kernel on
``PYTHONPATH=/opt/kernel`` (it is a flat ``package=false`` project), so its
modules import as the top-level ``pipeline`` / ``canonical`` packages — NOT as
``kernel.pipeline``.

Deployed on AgentCore Runtime via ``BedrockAgentCoreApp``; ``app.run()`` is
called at module import time (not behind ``__main__``) so the container's
import of this module starts the invoke server — see the bottom of the file.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import boto3  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]

# Strands SDK (installed in the container; untyped third-party).
from strands import Agent, tool  # type: ignore[import-not-found]

# Kernel — Ashwani's deterministic pipeline (on PYTHONPATH=/opt/kernel).
from canonical.model import ClassificationRow, r2  # type: ignore[import-not-found]
from pipeline import compute as k_compute  # type: ignore[import-not-found]
from pipeline import extract as k_extract  # type: ignore[import-not-found]
from pipeline import pivot as k_pivot  # type: ignore[import-not-found]

from steering import ExtractorSteering

ENV = os.environ.get("ENV", "dev")
INPUTS_BUCKET = os.environ.get("INPUTS_BUCKET", "")
OUTPUTS_BUCKET = os.environ.get("OUTPUTS_BUCKET", "")
PROFILES_DIR = os.environ.get("PROFILES_DIR", "/opt/profiles")
BEDROCK_GUARDRAIL_ID = os.environ.get("BEDROCK_GUARDRAIL_ID", "")
SCRATCH = os.environ.get("AGENT_SCRATCH", "/tmp/agent-runs")  # AgentCore /tmp scratch

s3 = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime")

with open(os.path.join(os.path.dirname(__file__), "system-prompt.md"), encoding="utf-8") as _f:
    EXTRACTOR_SYSTEM_PROMPT = _f.read()


# --- helpers ----------------------------------------------------------------
def _list_s3_objects(prefix: str) -> list[str]:
    paginator = s3.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=INPUTS_BUCKET, Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def _load_profile(union: str) -> dict[str, Any]:
    with open(os.path.join(PROFILES_DIR, f"{union}.yaml"), encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f)
    return data


def _serialize(row: ClassificationRow) -> dict[str, Any]:
    # ClassificationRow is a dataclass; fall back to __dict__ for the wire form.
    return getattr(row, "__dict__", {"value": row})


def _deserialize(data: dict[str, Any]) -> ClassificationRow:
    return ClassificationRow(**data)  # type: ignore[no-any-return]


# --- tools (thin kernel wrappers) -------------------------------------------
@tool
def stage_inputs_from_s3(union: str, s3_prefix: str) -> dict[str, Any]:
    """Download the union's PDFs from S3 into the kernel's ``data/<union>/cba/``."""
    union_dir = f"{SCRATCH}/{union}"
    os.makedirs(f"{union_dir}/cba", exist_ok=True)
    keys = _list_s3_objects(s3_prefix)
    for key in keys:
        s3.download_file(INPUTS_BUCKET, key, f"{union_dir}/cba/{os.path.basename(key)}")
    return {"union_dir": union_dir, "files": len(keys)}


@tool
def run_kernel_extractor(union: str, union_dir: str) -> dict[str, Any]:
    """Run the kernel's per-union deterministic extractor. Returns rows + gaps."""
    extractor_fn = k_extract.EXTRACTORS[union]
    rows, gaps = extractor_fn(union_dir)
    return {"rows": [_serialize(r) for r in rows], "gaps": gaps, "gap_count": len(gaps)}


@tool
def compute_derived_columns(union: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the kernel's half-up-rounded derived-column rules (Profile YAML)."""
    profile = _load_profile(union)
    return [_serialize(k_compute.resolve_row(profile, _deserialize(r))) for r in rows]


@tool
def pivot_to_ratesheet_csv(
    union: str, rows: list[dict[str, Any]], out_s3_key: str
) -> dict[str, Any]:
    """Write the ratesheet CSV (matching groundtruth header) and upload to S3."""
    profile = _load_profile(union)
    local_csv = f"{SCRATCH}/{union}/output.csv"
    n_rows = k_pivot.write_csv(profile, [_deserialize(r) for r in rows], local_csv)
    s3.upload_file(local_csv, OUTPUTS_BUCKET, out_s3_key)
    return {"s3_key": out_s3_key, "rows_written": n_rows}


@tool
def escalate_to_claude_multimodal(
    s3_key: str, profile_aliases: dict[str, Any], missing_fields: list[str]
) -> dict[str, Any]:
    """Path C: ask Bedrock Claude Sonnet for ONLY the kernel's missing fields."""
    pdf_bytes = s3.get_object(Bucket=INPUTS_BUCKET, Key=s3_key)["Body"].read()
    prompt = (
        "Read ONLY the following fields from the attached Rate Notice and return "
        f"them as JSON. Do not guess; omit any you cannot read. Fields: {missing_fields}. "
        f"Label aliases: {profile_aliases}."
    )
    body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4000,
        "system": EXTRACTOR_SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": base64.b64encode(pdf_bytes).decode(),
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    kwargs: dict[str, Any] = {
        "modelId": "us.anthropic.claude-sonnet-4-6-v1:0",
        "body": json.dumps(body),
    }
    if BEDROCK_GUARDRAIL_ID:
        kwargs["guardrailIdentifier"] = BEDROCK_GUARDRAIL_ID
        kwargs["guardrailVersion"] = "DRAFT"
    response = bedrock.invoke_model(**kwargs)
    payload = json.loads(response["body"].read())
    return {"fields": payload, "requested": missing_fields}


@tool
def validate_total_package_checksum(union: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify wage + fringes equals the printed Total Package (±$0.05)."""
    journeyman = next((r for r in rows if r.get("classification") == "Journeyman"), None)
    if journeyman is None:
        return {"passed": None, "reason": "no Journeyman row found"}
    cells = journeyman.get("cells", {})
    fringe_prefixes = ("health_welfare", "pension", "sis", "annuity", "industry")
    computed = cells.get("wage", {}).get("value", 0.0) + sum(
        c["value"]
        for c in cells.values()
        if str(c.get("canonical_field", "")).startswith(fringe_prefixes)
    )
    expected = journeyman.get("notice_total")
    if expected is None:
        return {"passed": None, "reason": "notice did not print a Total Package"}
    return {
        "passed": abs(computed - expected) <= 0.05,
        "computed": r2(computed),
        "expected": expected,
        "diff": r2(computed - expected),
    }


def build_agent() -> Agent:
    """Construct the Strands ExtractorAgent with steering."""
    return Agent(
        name="ExtractorAgent",
        system_prompt=EXTRACTOR_SYSTEM_PROMPT,
        tools=[
            stage_inputs_from_s3,
            run_kernel_extractor,
            compute_derived_columns,
            pivot_to_ratesheet_csv,
            escalate_to_claude_multimodal,
            validate_total_package_checksum,
        ],
        plugins=[ExtractorSteering()],
        trace_attributes={"service": "laboraid-extractor", "env": ENV},
    )


# --- AgentCore Runtime entrypoint -------------------------------------------
try:  # pragma: no cover - only present in the deployed container
    from bedrock_agentcore.runtime import (  # type: ignore[import-not-found]
        BedrockAgentCoreApp,
    )

    app = BedrockAgentCoreApp()

    @app.entrypoint  # type: ignore[misc]
    def invoke(payload: dict[str, Any]) -> Any:
        """AgentCore Runtime entrypoint — payload carries the union + S3 prefix."""
        agent = build_agent()
        return agent(payload.get("prompt", json.dumps(payload)))

    # Run unconditionally when the AgentCore SDK is importable. AgentCore loads
    # this module on container start (it does NOT run it as __main__), so the
    # server MUST start here — gating on `__name__ == "__main__"` would leave the
    # entrypoint registered but never listening, and the container would exit
    # immediately (audit B7 / decision D-B7).
    app.run()
except ImportError:  # pragma: no cover - local dev / unit tests without AgentCore SDK
    # The Strands @tool functions and build_agent() remain importable so unit
    # tests can exercise the agent logic without the AgentCore runtime.
    pass
