"""E.3 — draft_extractor_python: Bedrock Sonnet emits a per-union Python extractor.

Same dual-mode (ANTHROPIC_API_KEY → direct; AWS creds → Bedrock; neither →
RuntimeError) as draft_profile.py / extract_generic.py.

The system prompt instructs Claude to output plain Python source for
``extract_<local>(union_dir) -> (rows, gaps)`` modeled on ``extract_704`` from
``kernel/pipeline/extract.py``. The Rate Notice PDF is attached as a Bedrock
document part so the model can derive numbers from it directly. The
never-fabricate rule is encoded in the prompt.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

_MODEL_ID = "us.anthropic.claude-sonnet-4-6-v1:0"
_ANTHROPIC_MODEL = "claude-sonnet-4-6-20250930"


_SYSTEM_PROMPT = """You are writing a deterministic Python extractor for one union
in the LaborAid Rate Engine.

OUTPUT: only the Python source for ONE function. No markdown fences, no prose.
Start at column 1 with `def extract_<local>(union_dir):` (NOT inside any class).

PRIME DIRECTIVE — NEVER FABRICATE:
The function MUST NOT invent, guess, or interpolate any rate value. Every
numeric cell emitted MUST trace to text in the attached Rate Notice PDF or
to a derived rule documented in the profile YAML. If a value cannot be read,
the function MUST record a gap and leave the cell unset.

SIGNATURE:
    def extract_<local>(union_dir):
        # ...
        return rows, gaps

where:
    rows: list[ClassificationRow] from `canonical.model`
    gaps: list[tuple[str, str, str, str]]  # (zone, package, column, reason)

PATTERN TO FOLLOW — `extract_704` in kernel/pipeline/extract.py:

    def extract_704(union_dir):
        notice = f"{union_dir}/cba/<notice filename>"
        cba = f"{union_dir}/cba/<cba filename>"
        ND, CD = os.path.basename(notice), os.path.basename(cba)
        rows, gaps = [], []

        # ... parse the PDF into a per-classification dict of fund values ...

        def emit_row(pkg, order, wage, rec, wage_loc):
            row = ClassificationRow("Building", pkg, order)
            row.add(RateCell("Building", pkg, order, "wage", r2(wage), "$", ND, wage_loc))
            # ... add fund cells ...
            rows.append(row)

        emit_row("Journeyman", 90, jw, jrec, 'notice "Journeyman\\'s Wage"')
        # ... emit foreman / apprentice rows ...
        return rows, gaps

IMPORTS YOU MAY USE (the kernel container places these on PYTHONPATH=/opt/kernel):

    from canonical.model import ClassificationRow, RateCell, r2
    from pipeline import ingest          # for is_image_only() etc.
    from pipeline import ocr             # only if OCR is needed
    import pdfplumber
    import os, re

RULES:
1. Function name MUST be exactly `extract_<local>` where <local> is the
   union's local number (digits only).
2. Single positional arg `union_dir`. No *args, no **kwargs.
3. Every return statement MUST return a 2-tuple `(rows, gaps)`.
4. Use the canonical_field NAMES from the provided fields.yaml (e.g. "wage",
   "health_welfare"). Each RateCell carries the source_doc filename + a
   short source_locator string (e.g. "notice page 2 / table 1 / row 3").
5. NEVER write a value the PDF does not contain. If a fund isn't on the
   notice, append a gap and leave the cell out — do NOT copy from
   groundtruth.
6. Foreman / General Foreman / Apprentice rows are derived from the CBA
   article rules in the profile YAML — they are NOT separately printed
   on most Rate Notices. Read those rules from the profile + apply them.
"""


def draft_extractor_python(
    union: str,
    profile_yaml: str,
    sample_rate_notice_path: str = "",
) -> str:
    """Call Claude Sonnet to produce a `def extract_<local>(...)` function.

    Args:
        union: kernel union key, e.g. ``"sprinkler_fitters_120"``.
        profile_yaml: the YAML string previously drafted by E.2.
        sample_rate_notice_path: optional path to the Rate Notice PDF that
            should be attached to the Bedrock call. If empty or the file
            doesn't exist, the prompt runs text-only.

    Returns:
        Python source as a string (markdown fences stripped if present).
    """
    pdf_bytes: bytes | None = None
    if sample_rate_notice_path:
        p = Path(sample_rate_notice_path)
        if p.exists() and p.suffix.lower() == ".pdf":
            pdf_bytes = p.read_bytes()

    user_text = _build_user_prompt(union, profile_yaml)

    if os.environ.get("ANTHROPIC_API_KEY"):
        raw = _call_anthropic_direct(user_text, pdf_bytes)
    elif _has_aws_creds():
        raw = _call_bedrock(user_text, pdf_bytes)
    else:
        raise RuntimeError("No LLM creds — cannot draft")

    return _strip_fences(raw)


def _has_aws_creds() -> bool:
    """Match draft_profile._has_aws_creds; duplicated to keep modules independent."""
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        return True
    if os.environ.get("AWS_PROFILE"):
        return True
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return True
    if os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"):
        return True
    if os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE"):
        return True
    if os.path.exists(os.path.expanduser("~/.aws/credentials")):
        return True
    return False


def _build_user_prompt(union: str, profile_yaml: str) -> str:
    """Compact user message describing what to emit."""
    # Extract the local number from the union key (best-effort).
    m = re.search(r"_(\d+)$", union)
    local = m.group(1) if m else union.split("_")[-1]
    return (
        f"Union: {union}\n"
        f"Local number: {local}\n"
        f"Required function name: extract_{local}\n\n"
        "Profile YAML for this union (defines the output columns + derived rules):\n"
        "```yaml\n"
        f"{profile_yaml.strip()}\n"
        "```\n\n"
        "The Rate Notice PDF is attached. Produce the Python source now. "
        "Output ONLY the function source — no prose, no markdown fences."
    )


def _call_bedrock(user_text: str, pdf_bytes: bytes | None) -> str:
    """Production path — Bedrock Runtime InvokeModel with optional PDF document."""
    import boto3  # type: ignore[import-untyped]

    client = boto3.client("bedrock-runtime")

    content: list[dict[str, Any]] = []
    if pdf_bytes is not None:
        content.append(
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(pdf_bytes).decode(),
                },
            }
        )
    content.append({"type": "text", "text": user_text})

    body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8000,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": content}],
    }
    kwargs: dict[str, Any] = {"modelId": _MODEL_ID, "body": json.dumps(body)}
    guardrail_id = os.environ.get("BEDROCK_GUARDRAIL_ID")
    if guardrail_id:
        kwargs["guardrailIdentifier"] = guardrail_id
        kwargs["guardrailVersion"] = "DRAFT"
    response = client.invoke_model(**kwargs)
    payload = json.loads(response["body"].read())
    parts = payload.get("content", [{}])
    if parts and isinstance(parts, list):
        first = parts[0]
        if isinstance(first, dict):
            return str(first.get("text", ""))
    return ""


def _call_anthropic_direct(user_text: str, pdf_bytes: bytes | None) -> str:
    """Local dev path — direct Anthropic API with optional PDF document."""
    import anthropic  # type: ignore[import-untyped]

    client = anthropic.Anthropic()

    content: list[dict[str, Any]] = []
    if pdf_bytes is not None:
        content.append(
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(pdf_bytes).decode(),
                },
            }
        )
    content.append({"type": "text", "text": user_text})

    response = client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=8000,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],  # type: ignore[typeddict-item]
    )
    if not response.content:
        return ""
    first = response.content[0]
    text = getattr(first, "text", "")
    return str(text)


def _strip_fences(text: str) -> str:
    """Remove ```python / ``` fences if Claude wrapped despite instructions."""
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:python|py)?\s*\n?", "", stripped)
        stripped = re.sub(r"\n?```\s*$", "", stripped)
    return stripped.strip() + "\n"
