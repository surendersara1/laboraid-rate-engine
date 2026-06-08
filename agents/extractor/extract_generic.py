"""Path C — generic LLM-based extractor for unknown unions.

Architecture role: when `union not in kernel.pipeline.extract.EXTRACTORS`, the
ExtractorAgent calls this module instead of `run_kernel_extractor`. It sends the
union's Rate Notice PDF + the column shape from the customer's existing
groundtruth rate sheet to Claude Sonnet 4.6, and parses the JSON response into
the same `ClassificationRow` objects the deterministic kernel emits.

Dual-mode invocation:
  * Production (AgentCore Runtime on AWS): uses ``boto3.client('bedrock-runtime')``
  * Local dev workstation: uses the ``anthropic`` SDK with ``ANTHROPIC_API_KEY``
    env var, so ``process_customer_samples.py`` can run without AWS credentials.

Returns the same ``(rows, gaps)`` tuple shape as kernel extractors so the rest
of the pipeline (compute → pivot → evaluate) is unchanged.

Never-fabricate rule: if Claude returns ``null`` for a cell or omits it, we
record a gap with reason ``"claude did not extract"`` — same blanking semantics
the kernel uses when pdfplumber/OCR can't read a cell.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

# Kernel imports — same path conventions the agent already uses.
from canonical.model import ClassificationRow, RateCell  # type: ignore[import-not-found]


_MODEL_ID = "us.anthropic.claude-sonnet-4-6"  # cross-region inference profile (verified via aws bedrock list-inference-profiles in us-east-2)
_ANTHROPIC_MODEL = "claude-sonnet-4-6-20250930"  # direct Anthropic API name


def _cached_system(prompt: str) -> list[dict[str, Any]]:
    """Wrap the (large, static) system prompt in a cache_control block so repeated
    extractions reuse the cached prefix (Bedrock + Anthropic both honour this)."""
    return [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}]

_SYSTEM_PROMPT = """You are extracting a union construction trade ratesheet from a Rate Notice PDF.

PRIME DIRECTIVE — NEVER FABRICATE:
You MUST NOT invent, guess, or interpolate any rate value. Every numeric cell
you emit MUST come directly from text or tables visible in the attached PDF.
If a cell is not in the PDF, set its value to null and explain in the
source_locator field (e.g., "not in this notice"). A blank cell is correct;
a fabricated cell is a defect.

OUTPUT FORMAT:
Return ONLY a JSON object matching this schema. No prose, no markdown fences.

{
  "rows": [
    {
      "zone": "Building" | "Residential" | "Power & Gas" | etc,
      "classification": "Foreman" | "Journeyman" | "Apprentice Year 5" | etc,
      "class_order": <int, descending pay — Foreman highest>,
      "cells": {
        "<column_name_exactly_as_provided>": {
          "value": <number or null>,
          "source_locator": "page <N> / table <T> / row <R>" | "derived" | "not in this notice",
          "confidence": <float 0.0-1.0>
        },
        ...
      }
    },
    ...
  ]
}

RULES:
1. The set of zones + classifications must be inferred from the PDF — typical
   sets are "Building" (always present), optionally "Residential" or
   "Power & Gas", with classifications like Foreman, Journeyman, and a ladder
   of Apprentice Year 1..5 or Class 1..10.
2. class_order: 100 = General Foreman, 98 = Foreman, 90 = Journeyman,
   11..15 = Apprentice years 1..5 (descending pay). Approximate is fine.
3. Use the EXACT column names provided in the user message. Do not invent
   columns; do not omit columns. If a column has no source in the PDF, the
   cell goes in with value=null + locator="not in this notice".
4. Numeric values only — no dollar signs, no commas. e.g., 54.70, not "$54.70".
5. Percentages as strings with trailing %, e.g., "6.00%".
6. confidence: 0.95 for clean text PDFs, 0.80 for OCR'd values, 0.50 for
   anything you're uncertain about, 0.0 for null cells.
"""


# ---------------------------------------------------------------------------
# Public entry — Path C
# ---------------------------------------------------------------------------

def extract_via_claude(
    union_dir: str,
    union: str,
) -> tuple[list[ClassificationRow], list[tuple[str, str, str, str]]]:
    """Run Claude on the union's Rate Notice and return canonical rows + gaps.

    Args:
        union_dir: filesystem path like ``data/sprinkler_fitters_120``
        union: kernel union key (e.g., ``"sprinkler_fitters_120"``)

    Returns:
        Tuple of (ClassificationRow list, gaps list). Same shape as kernel
        extractors so downstream compute/pivot/evaluate run unchanged.
    """
    union_path = Path(union_dir)
    rate_notice = _locate_rate_notice(union_path)
    if rate_notice is None:
        return [], [("(global)", "(any)", "(any)", "no Rate Notice PDF found")]

    columns = _read_groundtruth_columns(union_path)
    if not columns:
        return [], [("(global)", "(any)", "(any)", "no groundtruth ratesheet for column shape")]

    pdf_bytes = rate_notice.read_bytes()
    response_json = _invoke_claude(pdf_bytes, columns, union)
    rows, gaps = _parse_response(response_json, source_doc=rate_notice.name)
    return rows, gaps


# ---------------------------------------------------------------------------
# Inputs — pick Rate Notice + read groundtruth header
# ---------------------------------------------------------------------------

_NOTICE_PATTERNS = (
    re.compile(r"rate\s*notice", re.IGNORECASE),
    re.compile(r"wage\s*rate\s*notice", re.IGNORECASE),
    re.compile(r"wage\s*notice", re.IGNORECASE),
    re.compile(r"wage\s*sheet", re.IGNORECASE),
    re.compile(r"wage\s*rates", re.IGNORECASE),
    re.compile(r"rate\s*sheet", re.IGNORECASE),
)


def _locate_rate_notice(union_path: Path) -> Path | None:
    """Find the most recent Rate Notice PDF in the union's cba/ folder.

    Heuristic: filename contains 'Rate Notice' / 'Wage Notice' / 'Wage Sheet'
    / 'Wage Rates'. Picks the lexicographically last match (filenames lead
    with the period date, so last = newest).
    """
    cba_dir = union_path / "cba"
    if not cba_dir.exists():
        return None
    candidates = sorted(p for p in cba_dir.rglob("*.pdf") if _looks_like_notice(p.name))
    return candidates[-1] if candidates else None


def _looks_like_notice(name: str) -> bool:
    return any(pat.search(name) for pat in _NOTICE_PATTERNS)


def _read_groundtruth_columns(union_path: Path) -> list[str]:
    """Read the first row of the customer's existing rate sheet for column shape.

    Tries .csv first, then .xlsx. Skips anything that isn't a ratesheet
    (e.g., 'Articles', 'Summary'). Returns an empty list if nothing usable.
    """
    rs_dir = union_path / "ratesheet"
    if not rs_dir.exists():
        return []

    # CSV first — easiest to parse, no openpyxl needed for header.
    for csv in sorted(rs_dir.glob("*.csv"), reverse=True):
        try:
            with csv.open("r", encoding="utf-8-sig", newline="") as f:
                header = f.readline().strip()
                if header:
                    cols = [c.strip() for c in header.split(",") if c.strip()]
                    if cols:
                        return cols
        except OSError:
            continue

    # xlsx fallback — read header row with openpyxl.
    for xlsx in sorted(rs_dir.glob("*.xlsx"), reverse=True):
        if "articles" in xlsx.name.lower() or "summary" in xlsx.name.lower():
            continue
        try:
            import openpyxl  # type: ignore[import-untyped]
            wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
            ws = wb.active
            if ws is None:
                continue
            header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
            if header_row:
                cols = [str(c).strip() for c in header_row if c is not None and str(c).strip()]
                if cols:
                    return cols
        except Exception:
            continue

    return []


# ---------------------------------------------------------------------------
# Claude invocation — dual-mode (Bedrock OR Anthropic direct)
# ---------------------------------------------------------------------------

def _invoke_claude(pdf_bytes: bytes, columns: list[str], union: str) -> dict[str, Any]:
    """Call Claude. Bedrock if AWS creds available, else Anthropic direct."""
    user_text = (
        f"Union: {union}\n\n"
        f"Required columns (use these EXACT names in cells):\n"
        + "\n".join(f"  - {c}" for c in columns)
        + "\n\nExtract the ratesheet from the attached Rate Notice PDF. "
        "Return ONLY the JSON object per the schema. No prose."
    )

    if os.environ.get("ANTHROPIC_API_KEY"):
        return _call_anthropic_direct(pdf_bytes, user_text)
    return _call_bedrock(pdf_bytes, user_text)


def _call_bedrock(pdf_bytes: bytes, user_text: str) -> dict[str, Any]:
    """Production path — Bedrock Runtime InvokeModel with PDF document."""
    import boto3  # type: ignore[import-untyped]

    client = boto3.client("bedrock-runtime")
    body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8000,
        "system": _cached_system(_SYSTEM_PROMPT),
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
                    {"type": "text", "text": user_text},
                ],
            }
        ],
    }
    kwargs: dict[str, Any] = {"modelId": _MODEL_ID, "body": json.dumps(body)}
    guardrail_id = os.environ.get("BEDROCK_GUARDRAIL_ID")
    if guardrail_id:
        kwargs["guardrailIdentifier"] = guardrail_id
        kwargs["guardrailVersion"] = "DRAFT"
    try:
        response = client.invoke_model(**kwargs)
        payload = json.loads(response["body"].read())
    except Exception as e:  # Bedrock error, throttle, or malformed body
        raise RuntimeError(f"Bedrock invoke/parse failed ({_MODEL_ID}): {e}") from e
    text = payload.get("content", [{}])[0].get("text", "")
    return _extract_json_object(text)


def _call_anthropic_direct(pdf_bytes: bytes, user_text: str) -> dict[str, Any]:
    """Local dev path — direct Anthropic API. Requires ANTHROPIC_API_KEY env var."""
    import anthropic  # type: ignore[import-untyped]

    client = anthropic.Anthropic()  # picks up ANTHROPIC_API_KEY from env
    try:
        response = client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=8000,
            system=_cached_system(_SYSTEM_PROMPT),
            messages=[
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
                        {"type": "text", "text": user_text},
                    ],
                }
            ],
        )
    except Exception as e:
        raise RuntimeError(f"Anthropic messages.create failed ({_ANTHROPIC_MODEL}): {e}") from e
    text = response.content[0].text if response.content else ""
    return _extract_json_object(text)


def _extract_json_object(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of Claude's response, tolerating fences."""
    if not text:
        return {"rows": []}
    # Strip markdown fences if Claude wrapped despite instructions.
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"```\s*$", "", stripped)
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # Last-resort: find the first balanced {...}.
    start = stripped.find("{")
    end = stripped.rfind("}")
    if 0 <= start < end:
        try:
            obj2 = json.loads(stripped[start : end + 1])
            if isinstance(obj2, dict):
                return obj2
        except json.JSONDecodeError:
            pass
    return {"rows": []}


# ---------------------------------------------------------------------------
# Response → ClassificationRow + gaps
# ---------------------------------------------------------------------------

def _parse_response(
    response: dict[str, Any],
    source_doc: str,
) -> tuple[list[ClassificationRow], list[tuple[str, str, str, str]]]:
    """Convert Claude's JSON into ClassificationRow objects + a gaps list.

    Cells with value=null become gaps (zone, classification, column, reason).
    """
    rows: list[ClassificationRow] = []
    gaps: list[tuple[str, str, str, str]] = []
    for raw_row in response.get("rows") or []:
        zone = str(raw_row.get("zone", "(unknown)"))
        cls = str(raw_row.get("classification", "(unknown)"))
        order = int(raw_row.get("class_order", 50))
        row = ClassificationRow(zone=zone, classification=cls, class_order=order)
        for col_name, cell_obj in (raw_row.get("cells") or {}).items():
            if not isinstance(cell_obj, dict):
                continue
            value = cell_obj.get("value")
            locator = str(cell_obj.get("source_locator", ""))
            confidence = float(cell_obj.get("confidence", 0.0))
            canonical_field = _column_to_canonical(col_name)
            if value is None:
                gaps.append((zone, cls, col_name, locator or "claude did not extract"))
                continue
            kind = _value_kind(value)
            row.add(
                RateCell(
                    zone=zone,
                    classification=cls,
                    class_order=order,
                    canonical_field=canonical_field,
                    value=value,
                    value_kind=kind,
                    source_doc=source_doc,
                    source_locator=locator,
                    confidence=confidence,
                )
            )
        rows.append(row)
    return rows, gaps


def _column_to_canonical(column: str) -> str:
    """Best-effort column name → canonical field id (lower_snake_case).

    Path C can't perfectly match fields.yaml without per-union knowledge, so we
    fall back to a deterministic kebab→snake conversion. The downstream pivot
    step keys on the EXACT output column name from the profile, so canonical_field
    is more of an audit trail label here than a routing key.
    """
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", column.strip().lower())
    return cleaned.strip("_") or "unknown"


def _value_kind(value: Any) -> str:
    if isinstance(value, str) and value.endswith("%"):
        return "%"
    if isinstance(value, (int, float)):
        return "$"
    return "raw"
