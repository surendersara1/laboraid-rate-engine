"""LLM-driven extractor Lambda.

When the classifier says the union has no hand-coded kernel profile (any local
other than 537/704/483/281/821), the SFN routes here. We send the source PDF
to Bedrock Claude Sonnet 4.6 with a prompt that asks for a structured
extraction of every classification + every rate column visible in the
document. Claude's JSON response is converted into the same CSV shape the
deterministic kernel emits (Union Group, Trade, Union Local, Zone, Package,
Start Date, End Date, then N rate-value columns), uploaded to S3, and
returned to the SFN as `{s3_key, rows, gaps, gap_count, extracted_rows}` so
the Publisher Lambda treats it identically to a kernel extraction.

Never-fabricate rule: Claude is instructed to use `null` for any cell whose
source it cannot identify in the PDF. Those null cells flow through to
rate_cells with `value IS NULL` and are surfaced in the UI's gap report.

Input shape (called by extractor-invoker for unknown unions):
  {
    "classify": {"s3_key": "<input PDF>", "local": "111", "period": "YYYY-MM-DD", ...},
    "out_s3_key": "<canonical CSV output key>"
  }

Output shape — matches what the agent direct-mode returns:
  {"s3_key", "rows", "gaps", "gap_count", "extracted_rows", "checksum": null,
   "method": "llm_claude"}
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
from typing import Any

try:  # pragma: no cover - present in the Lambda runtime
    from aws_lambda_powertools import Logger, Tracer

    logger = Logger(service="laboraid-llm-extractor")
    tracer = Tracer()

    def _instrument(fn: Any) -> Any:
        return logger.inject_lambda_context(tracer.capture_lambda_handler(fn))

except ModuleNotFoundError:  # pragma: no cover - offline unit-test env
    import logging

    logger = logging.getLogger("laboraid-llm-extractor")  # type: ignore[assignment]

    def _instrument(fn: Any) -> Any:
        return fn


INPUTS_BUCKET = os.environ.get("INPUTS_BUCKET", "laboraid-dev-l3-bucket-inputs")
OUTPUTS_BUCKET = os.environ.get("OUTPUTS_BUCKET", "laboraid-dev-l3-bucket-outputs")
GUARDRAIL_ID = os.environ.get("BEDROCK_GUARDRAIL_ID", "")

# Cross-region inference profile for Claude Sonnet 4.6. Same model IDs the
# agent container uses. Sonnet is required (not Haiku) because the extraction
# needs structured JSON adherence + visual table understanding from PDF.
_MODEL_ID = "us.anthropic.claude-sonnet-4-6"


_RATE_NOTICE_PROMPT = """You extract a union construction trade rate sheet from a Rate Notice PDF.

PRIME DIRECTIVE — NEVER FABRICATE: every numeric cell you emit MUST come from
text/tables in the PDF. If a cell isn't in the PDF, use null. Blank is correct;
fabricated is a defect.

OUTPUT — return ONLY a single JSON object (no prose, no markdown fences) with
this compact shape. Use null (not 0) for missing values. Do NOT emit
source_locator or confidence — keep the output small to fit token limits:

{
  "union_local": "<local number, string>",
  "trade": "<Sprinkler|Plumber|Pipefitter|Electrician|Carpenter|Laborer|...>",
  "parent_intl": "<UA|IBEW|Carpenters|Laborers|...>",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD" or null,
  "zone": "Building",
  "columns": ["Wage", "<column names exactly as in the PDF>"],
  "rows": [
    {
      "classification": "Journeyman",
      "cells": {"Wage": 52.32, "Health & Welfare": 12.60, ...}
    },
    {"classification": "Apprentice Class 1", "cells": {"Wage": 20.93, ...}},
    ...
  ]
}

RULES:
1. Discover columns from the PDF — use the EXACT column names you see. Common:
   Wage, Wage 1.5x, Wage 2.0x, Wage Differential, Health & Welfare, Pension,
   Annuity, Apprenticeship Training, Industry Promotion, Retiree Holiday,
   S.U.B., Union Dues, Working Assessment, S&E Fund, Craft Fund, RESA.
2. Discover classifications. Typical ladder: General Foreman, Foreman,
   Journeyman, then Apprentice Class 1..10 (or Year 1..5) descending pay.
3. Every "columns" entry must appear as a key in every row's "cells"
   (use null if not present).
4. Numeric values only — no $ signs, no commas. e.g., 52.32.
5. Percentages as raw numbers without %: "6.00%" -> 6.00 (or 0.06 if decimal).
6. Compact form: values directly in "cells", NOT wrapped in {value, ...}.
"""

_CBA_PROMPT = """You extract the RESIDENTIAL Sprinkler package from a Collective
Bargaining Agreement (CBA) PDF.

CONTEXT — A CBA is a long prose contract covering many years. We've already
extracted the Building (Commercial) zone rates from a separate Rate Notice
PDF (which the kernel reads deterministically — you must NOT duplicate that
work). Your job here is ONLY the Residential Foreman + Journeyman package
that the Rate Notice doesn't carry.

SCOPE — emit Residential Foreman + Journeyman rows IF AND ONLY IF this
CBA actually contains a Residential Sprinkler / Residential Fire
Protection section with EXPLICIT wage values. Read the document first.
Two cases:

  CASE A — CBA HAS a Residential section with stated wage figures
  (e.g., "RESIDENTIAL SPRINKLER FITTER — Wage Rate $47.82 per hour"):
    → emit exactly 2 rows:
       Row 1: zone="Residential", classification="Foreman"
       Row 2: zone="Residential", classification="Journeyman"

  CASE B — CBA does NOT have a Residential section, OR mentions
  "Residential" only in scope/jurisdiction text without dollar values:
    → emit ZERO rows. Return {"rows": []}.

Many unions (e.g. 704) work only on Building/Commercial — their CBAs
have no Residential package. For those unions, emitting empty rows
creates phantom NULL cells the reviewer must then dismiss. Don't do it.

DO NOT EMIT APPRENTICE ROWS UNDER ANY CIRCUMSTANCES. Apprentice rates
come from a dedicated Wage Rate Sheet PDF that supersedes whatever the
CBA might suggest. Even if the CBA contains:
  - Article 15 Building apprentice percentages (Class 1 = 40%, ..., Class
    10 = 90%) — those are BUILDING only, and apprentices come from the
    Wage Rate Sheet anyway.
  - A statement like "rates for Residential Trainees shall be based on
    the Residential Fitters Rate".
  - An explicit dollar table for Residential apprentices.
…in EVERY case: emit ZERO Apprentice rows. The Wage Rate Sheet wins.

DO NOT EMIT BUILDING ROWS. Building comes from the Rate Notice (kernel).

PRIME DIRECTIVE — NEVER FABRICATE:
- Every numeric cell you emit MUST come VERBATIM from text in this PDF.
- If a cell is not stated in the PDF, emit null (not 0).
- Pension/Vacation allocations frequently depend on a separate package-
  reallocation notice that is NOT in this CBA. If the CBA states a base
  pension at one date and the user wants a different effective date, use
  null unless an explicit escalator is written.

CLASSIFICATION NAMES — use EXACTLY these:
  - "Foreman"     (NOT "Residential Foreman", NOT "General Foreman")
  - "Journeyman"  (NOT "Residential Sprinkler Fitter", NOT "Fitter")

COLUMN NAMES — use the customer's canonical names (adapt the local suffix):
  Wage, Wage Differential, Wage 1.5x, Wage 2.0x, Health & Welfare,
  Health & Welfare Metal, Pension, SIS, UA International Training,
  Industry Promotion National Use, J&A Training <local>, NCFPCG <local>,
  Bay Area IP Fund <local>, HRA <local>, Vacation <local>,
  Union Dues 1 <local>, Union Dues 2 <local>.

Common CBA label → canonical column mappings:
  Wage Rate                    → Wage
  Metal Trades Plan A          → Health & Welfare Metal
  N.A.S.I. Pension             → Pension
  HRA Contribution             → HRA <local>
  Local <N> Training Fund      → J&A Training <local>
  UA International Training    → UA International Training
  SIS Pension                  → SIS
  No. CA Fire Prot. Industry   → NCFPCG <local>
  Industry Promotion           → Industry Promotion National Use
  Industry Promotion (Bay Area)→ Bay Area IP Fund <local>

If the CBA says "Foreman's rate shall be $X over the Residential Sprinkler
Fitter rate", emit the Foreman row with Wage = Journeyman Wage + X (and
copy all other benefit columns from the Journeyman row unless the CBA
overrides them).

For Wage 1.5x and Wage 2.0x: emit if the CBA states the multipliers
("time and one half" → 1.5, "double time" → 2.0). For Residential, the
CBA may state Foreman gets the SAME 1x rate as the package (no premium
on the differential) — in that case Wage Differential = Wage.

OUTPUT — return ONLY a single JSON object (no prose, no markdown) shaped
exactly like:

{
  "union_local": "<local>",
  "trade": "Sprinkler",
  "parent_intl": "UA",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD" or null,
  "zone": "Residential",
  "columns": ["Wage", "Wage Differential", "Wage 1.5x", "Wage 2.0x",
              "Health & Welfare Metal", "Pension", "SIS",
              "UA International Training", "Industry Promotion National Use",
              "J&A Training <local>", "NCFPCG <local>",
              "Bay Area IP Fund <local>", "HRA <local>",
              "Vacation <local>", "Union Dues 1 <local>",
              "Union Dues 2 <local>"],
  "rows": [
    {"classification": "Foreman",    "cells": {"Wage": 50.82, ...}},
    {"classification": "Journeyman", "cells": {"Wage": 47.82, ...}}
  ]
}
"""

_APPRENTICE_SCALE_PROMPT = """You extract an Apprentice / Trainee wage scale
from a PDF. The PDF typically lists Apprentice Class 1..N (or Year 1..N,
or Period 1..N) with dollar wages + benefit fund line items per class.

ZONE DETECTION — read the document title and any narrative:
  - If the title or narrative says "RESIDENTIAL" or "Residential
    Sprinkler" or "Residential Apprentice" → emit rows with
    zone="Residential".
  - If the title or narrative says "COMMERCIAL" or no zone qualifier
    and the rates appear to be the union's general-purpose apprentices
    (often split by indenture date, like "Indentured After 7/1/2020")
    → emit rows with zone="Building" (our canonical name for Commercial).
  - If the document covers multiple zones, emit rows per zone, setting
    "zone" on each row.

INDENTURE DATE COHORTS — many unions (e.g. Sprinkler 281) split their
Apprentice Wage Sheets into separate PDFs by indenture date (one PDF
for apprentices indentured before a cutoff, another for after). Each
PDF carries a different scale. The filename will hint at which cohort
("Indentured After 07 2020", "Indentured Prior to 07 2020"). Emit one
set of rows per PDF; do NOT try to merge cohorts — each cohort's rows
go to its own (zone, package) tuple where the package name embeds the
cohort, e.g. "Apprentice Class 1 (indentured after 07/2020)".

PRIME DIRECTIVE — NEVER FABRICATE: source every cell from the PDF.

OUTPUT — same canonical shape as the Rate Notice prompt. Set zone per
row:

{
  "union_local": "<local>",
  "trade": "Sprinkler" | "Pipefitter" | ...,
  "parent_intl": "UA",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD" or null,
  "zone": "Building",
  "columns": ["Wage", "Wage 1.5x", "Wage 2.0x", "Health & Welfare", ...],
  "rows": [
    {"zone": "<Building|Residential>", "classification": "Apprentice Class 1", "cells": {"Wage": 21.96, ...}},
    {"zone": "<Building|Residential>", "classification": "Apprentice Class 2", "cells": {"Wage": 24.24, ...}},
    ...
  ]
}

RULES:
1. If the PDF expresses wages as a percentage of Journeyman, emit the
   percentage in a "Wage %" column and leave "Wage" null — Publisher's
   merge step will resolve the dollar value from a sibling Wage Sheet's
   Journeyman row.
2. If both percentage AND a separate dollar table are present, prefer
   the dollar table and put it in "Wage".
3. Extract ALL the benefit columns that appear (Health & Welfare,
   Pension, HRA, Vacation, Industry Promotion, training funds, etc.)
   using the customer's canonical column names with the local suffix
   substituted (e.g. "J&A Training 281", "S&E 537").
4. If the PDF has no extractable apprentice rates (e.g. the document
   only references a separate scale), return {"rows": []} rather than
   inventing values.
"""

_WAGE_RATE_SHEET_PROMPT = """You extract a COMPLETE union rate sheet from a multi-page
"Wage Rate Sheet" PDF. These are issued at major contract changes (e.g.,
new CBA effective date) and contain the FULL rate package — Building +
Residential, all classifications + apprentice tables — for one effective
date in a single document.

PRIME DIRECTIVE — NEVER FABRICATE: every numeric cell you emit MUST be
present in the PDF. Use null if not stated. The deterministic kernel may
also run on this same period; Publisher's merge mode will prefer the
kernel's values when they conflict, and use yours for cells the kernel
couldn't extract. Your job is to maximize COVERAGE without inventing.

TYPICAL LAYOUT (varies by union; a Sprinkler Fitters Wage Rate Sheet is
canonical, but Pipefitters, Plumbers, and other UA locals follow the
same broad structure with union-specific fund names):

  Page 1 — Building/Commercial Foreman + Journeyman, As-Per-Contract list:
    GENERAL FOREMAN $XX.XX  / FOREMAN 2 $XX.XX  / FOREMAN 1 $XX.XX  /
    JOURNEYMAN $XX.XX  / SUPPLEMENTAL PENSION  / NASI PENSION  /
    NASI HEALTH & WELFARE  / LOCAL <local> TRAINING FUND (J&A) / HRA /
    INTL TRAINING FUND / Industry-specific funds (NCFPCG for Sprinkler,
    others vary by trade) / INDUSTRY PROMOTION / INDUSTRY PROMOTION (Bay Area)
    Plus Work Assessment + Vacation rules in narrative form.

  Page 2 — Commercial Apprentice Classification table (Class 1..10 + Fitter)
    With columns: Rate/HR, Shift Work 15%, Vac. W/H, Work Asses,
    Work Asses II, H&W, HRA, PENS, S.I.S., *I.P., Int. Trng. Fund,
    **NCFPCG, J&A Trng. Cont., Total Package.

  Page 3 — Residential Foreman + Journeyman with benefit list (similar
    to Page 1 but for Residential), plus Residential-specific Work
    Assessment + Vacation rules.

  Page 4 — Residential Apprentice 1..5 individual rate blocks (NOT a
    tabular grid — each apprentice is its own labeled paragraph with
    Wage + benefit list).

OUTPUT — return ONLY a single JSON object (no prose, no markdown). The
key difference from other prompts: rows can have DIFFERENT zones in the
same response. Set zone PER ROW.

{
  "union_local": "<local>",
  "trade": "Sprinkler",
  "parent_intl": "UA",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD" or null,
  "zone": "Building",   /* default zone for rows that don't override */
  "columns": [
    "Wage", "Wage Differential", "Wage 1.5x", "Wage 2.0x",
    "Health & Welfare", "Health & Welfare Metal", "RESA", "Pension",
    "SIS", "UA International Training",
    "Industry Promotion National Use",
    "J&A Training <local>", "NCFPCG <local>", "Bay Area IP Fund <local>",
    "HRA <local>", "Vacation <local>",
    "Union Dues 1 <local>", "Union Dues 2 <local>"
  ],
  "rows": [
    /* === Building zone === */
    {"zone": "Building", "classification": "General Foreman", "cells": {...}},
    {"zone": "Building", "classification": "Foreman 2",       "cells": {...}},
    {"zone": "Building", "classification": "Foreman 1",       "cells": {...}},
    {"zone": "Building", "classification": "Journeyman",      "cells": {...}},
    {"zone": "Building", "classification": "Apprentice Class 10", "cells": {...}},
    /* ... Apprentice Class 9..1 ... */
    /* === Residential zone === */
    {"zone": "Residential", "classification": "Foreman",    "cells": {...}},
    {"zone": "Residential", "classification": "Journeyman", "cells": {...}},
    {"zone": "Residential", "classification": "Apprentice Class 5", "cells": {...}},
    /* ... Apprentice Class 4..1 ... */
  ]
}

CLASSIFICATION NAMES — use EXACTLY these, no other variants:
  Building   → "General Foreman", "Foreman 2", "Foreman 1", "Journeyman",
               "Apprentice Class 10" .. "Apprentice Class 1"
  Residential→ "Foreman", "Journeyman",
               "Apprentice Class 5" .. "Apprentice Class 1"
  (Note: page 2 calls the top row "Fitter" — map it to "Journeyman".
   Page 4's "RESIDENTIAL APPRENTICE N" → "Apprentice Class N".)

CANONICAL COLUMN MAPPINGS (PDF label → canonical column). Substitute
the actual local number from the document for <local>:
  Rate/HR              → Wage
  Shift Work 15%       → Wage Differential
  H&W / NASI Health & Welfare         → Health & Welfare  (Building zone only)
  NASI HEALTH & WELFARE (Residential) → Health & Welfare Metal
  PENS / NASI Pension / Pension Fund  → Pension
  S.I.S. / SIS Pension / SIS Fund     → SIS
  HRA / HRA Contribution              → HRA <local>
  Int. Trng. Fund / INTL Training Fund / UA International Training → UA International Training
  NCFPCG / No. CA Fire Prot Industry Fund → NCFPCG <local>  (Sprinkler-specific)
  J&A Trng. Cont. / Local <N> Training Fund / Apprenticeship Training → J&A Training <local>
  *I.P. / Industry Promotion / IAF    → Industry Promotion National Use
  Industry Promotion (Bay Area)       → Bay Area IP Fund <local>
  Vac. W/H + Vacation Withholding narrative → Vacation <local>
  Work Asses (6%, written as "6%")    → Union Dues 1 <local>: 0.06
  Work Asses II ($1.05)               → Union Dues 2 <local>: 1.05
  PAC / Political Action Committee    → PAC <local> (often $0 by default)
  Trade-specific funds — emit with the trade's canonical name, suffixed
  with <local>: e.g., "S&E <local>", "Craft <local>", "S.U.B. <local>",
  "Retiree Holiday <local>".

RESIDENTIAL-SPECIFIC RULES (only if the document carries them — many
Sprinkler Wage Rate Sheets have these on page 3; some unions don't have
a Residential section at all):
  - Work Assessment #1 for Residential Foreman/Journeyman = 6% of wage
    → Union Dues 1 <local>: 0.06
  - Work Assessment #1 for Apprentice 3, 4, 5 = $0.50/hr
    → Union Dues 1 <local>: 0.5  (for Apprentice 3, 4, 5)
    → Union Dues 1 <local>: 0    (for Apprentice 1, 2)
  - Work Assessment #2 ONLY applies to Residential Foreman/Journeyman
    → Union Dues 2 <local>: 1.05 for F/J, 0 for all Apprentices
  - Vacation Withholding ONLY for Residential Foreman/Journeyman
    → Vacation <local>: 0.50 for F/J, 0 for Apprentices

If the document has no Residential section, do NOT emit Residential
rows. Building-only is a valid result.

WAGE DIFFERENTIAL — for Residential Foreman/Journeyman, set Wage
Differential = Wage (no shift premium when the document says
"Foreman gets the same 1x rate"). For Building it's typically Wage ×
1.15 (shown explicitly as the "Shift Work 15%" column).

WAGE 1.5x AND 2.0x — compute as Wage × 1.5 and Wage × 2.0 respectively
ONLY when the PDF states the multipliers somewhere (typical: "time and
one half" / "double time"). Most Wage Rate Sheets establish these
implicitly through a Building Apprentice table where you can read
Rate/HR vs Shift Work 15% — the same multipliers apply across all
classifications. Compute them; do NOT leave them null when you have
a Wage value.

If the PDF only contains a partial subset (e.g., just Commercial
Apprentices), still emit the rows you can read — do NOT add Residential
rows from your memory of typical layouts. Use null for the rest.
"""

# Shape kept for backward compatibility — anything calling SYSTEM_PROMPT
# directly gets the rate-notice prompt.
SYSTEM_PROMPT = _RATE_NOTICE_PROMPT


def _prompt_for_doc_type(
    doc_type: str, local: str | int | None = None
) -> tuple[str, str]:
    """Return (system_prompt, user_instruction) for the given doc type.

    When `local` is provided, the system prompt is built via the SOP
    framework (Dan's SOP §2 + §4 + per-doc-type body + per-union master
    list context) — see lambdas/shared/sop_prompt.py. When `local` is
    None we fall back to the legacy hand-tuned prompts for back-compat.
    """
    if local is not None:
        try:
            import sop_prompt

            return (
                sop_prompt.build_system_prompt(doc_type, local),
                sop_prompt.build_user_instruction(doc_type),
            )
        except ImportError:
            logger.warning(
                "sop_prompt not importable (Lambda layer missing?) — falling "
                "back to legacy doc-type prompts"
            )
    # Legacy fallback (pre-SOP prompts) — kept for back-compat only.
    dt = (doc_type or "").lower()
    if dt == "cba":
        return (
            _CBA_PROMPT,
            "Extract the Residential Sprinkler package and any Apprentice "
            "scale stated in this CBA. Return the JSON exactly as specified.",
        )
    if dt == "apprentice_scale":
        return (
            _APPRENTICE_SCALE_PROMPT,
            "Extract the Apprentice/Trainee wage scale from this PDF. "
            "Return the JSON exactly as specified.",
        )
    if dt == "rate_sheet":
        return (
            _WAGE_RATE_SHEET_PROMPT,
            "Extract EVERY classification (Building + Residential, "
            "Foreman/Journeyman + every Apprentice Class) from this "
            "multi-page Wage Rate Sheet. Return the JSON exactly as "
            "specified, with zone set per row.",
        )
    return (
        _RATE_NOTICE_PROMPT,
        "Extract every classification and every rate column visible in this "
        "Rate Notice. Return the JSON exactly as specified.",
    )


def _invoke_bedrock(
    pdf_bytes: bytes,
    doc_type: str = "",
    local: str | int | None = None,
) -> dict[str, Any]:
    """Call Bedrock Claude with the PDF and parse its JSON response.

    boto3's default read_timeout on bedrock-runtime is 60s, but a 3MB PDF +
    several thousand output tokens routinely takes 90-180s. We give the call
    14 minutes of headroom (Lambda's 15-min ceiling) and disable retries so a
    slow first call doesn't double-charge the API.
    """
    import boto3
    from botocore.config import Config

    bedrock = boto3.client(
        "bedrock-runtime",
        config=Config(
            read_timeout=840,
            connect_timeout=10,
            retries={"max_attempts": 1},
        ),
    )
    system_prompt, user_instruction = _prompt_for_doc_type(doc_type, local)
    body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 16000,
        "system": [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
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
                    {"type": "text", "text": user_instruction},
                ],
            }
        ],
    }
    kwargs: dict[str, Any] = {"modelId": _MODEL_ID, "body": json.dumps(body)}
    if GUARDRAIL_ID:
        kwargs["guardrailIdentifier"] = GUARDRAIL_ID
        kwargs["guardrailVersion"] = "DRAFT"
    resp = bedrock.invoke_model(**kwargs)
    raw = resp["body"].read()
    payload = json.loads(raw)
    # Claude responses come back as content blocks; concat text blocks.
    text_blocks = [
        b.get("text", "")
        for b in payload.get("content", [])
        if b.get("type") == "text"
    ]
    text = "\n".join(text_blocks).strip()
    # Defensive: strip ```json fences if Claude adds them despite the prompt.
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    # Find the JSON object by balanced-brace scanning. Claude sometimes wraps
    # its response in prose ("Here is the extraction: {...}") or appends a
    # trailing comment; extracting just the {...} prevents that from breaking
    # the parse.
    json_text = _extract_balanced_object(text)
    parsed = None
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as e:
        # Some Claude responses include trailing commas in tables of cells —
        # strip them and retry once before giving up.
        cleaned = re.sub(r",(\s*[}\]])", r"\1", json_text)
        try:
            parsed = json.loads(cleaned)
            logger.info(
                "llm-extractor: parsed Claude JSON after trailing-comma cleanup"
            )
        except json.JSONDecodeError as e2:
            logger.warning(
                "could not parse Claude JSON even after cleanup: %s (orig %s)",
                e2,
                e,
            )
    if parsed is not None:
        return parsed
    # Last resort — write the raw response to S3 for debugging then return a
    # stub so the SFN doesn't 5-min-timeout while waiting for a re-parse.
    return _write_raw_for_debug(text)


def _extract_balanced_object(text: str) -> str:
    """Return the substring of text that starts with the first '{' and ends
    with its matching '}' (handles nested braces + strings)."""
    start = text.find("{")
    if start < 0:
        return text
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]  # unbalanced, return what we have


def _write_raw_for_debug(text: str) -> dict[str, Any]:
    """Persist the raw Claude response to S3 so we can read it back to debug
    the parse failure. Returns an empty-extraction stub."""
    import boto3

    s3 = boto3.client("s3")
    debug_key = f"llm-extractor-debug/{int(__import__('time').time())}.txt"
    try:
        s3.put_object(
            Bucket=OUTPUTS_BUCKET,
            Key=debug_key,
            Body=text.encode("utf-8"),
            ContentType="text/plain",
            ServerSideEncryption="aws:kms",
        )
        logger.warning(
            "llm-extractor: wrote unparseable Claude response to s3://%s/%s",
            OUTPUTS_BUCKET,
            debug_key,
        )
    except Exception:
        logger.exception("llm-extractor: failed to persist debug payload")
    return {
        "_parse_error": "could not parse Claude JSON",
        "_debug_key": debug_key,
        "_raw_head": text[:2000],
        "rows": [],
        "columns": [],
    }


def _to_canonical_csv(
    payload: dict[str, Any], classify: dict[str, Any]
) -> tuple[str, list[tuple[str, str, str, str]]]:
    """Convert Claude's structured response into the canonical CSV shape +
    gap list. The CSV layout mirrors what the kernel emits so the Publisher
    Lambda handles both extractors identically.
    """
    # Use classifier fallbacks for any field Claude didn't return.
    union_group = (
        payload.get("parent_intl") or "UNKNOWN"
    ).upper() or "UNKNOWN"
    trade_raw = payload.get("trade") or ""
    if not trade_raw:
        union_kernel = (classify.get("union") or "").lower()
        if union_kernel.startswith("local_"):
            trade_raw = "Unknown"
        else:
            trade_raw = union_kernel.split("_")[0].title() if union_kernel else "Unknown"
    local = str(
        payload.get("union_local") or classify.get("local") or ""
    ).strip()
    start_date = payload.get("start_date") or classify.get("period") or ""
    end_date = payload.get("end_date") or ""
    zone = payload.get("zone") or "Building"

    columns = list(payload.get("columns") or [])
    # Dedup + preserve order.
    seen: set[str] = set()
    columns = [c for c in columns if not (c in seen or seen.add(c))]
    rows = payload.get("rows") or []

    header = [
        "Union Group",
        "Trade",
        "Union Local",
        "Zone",
        "Package",
        "Start Date",
        "End Date",
        *columns,
    ]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)

    gaps: list[tuple[str, str, str, str]] = []
    extracted = 0
    for row in rows:
        cls = row.get("classification") or ""
        cells = row.get("cells") or {}
        row_zone = row.get("zone") or zone
        line: list[Any] = [
            union_group,
            trade_raw,
            local,
            row_zone,
            cls,
            start_date,
            end_date,
        ]
        for col in columns:
            cell = cells.get(col)
            # New compact schema: cells map directly to scalar values (or null).
            # Legacy verbose schema: {"value": x, "source_locator": "...", ...}.
            # Tolerate both so we don't have to re-prompt on schema drift.
            if isinstance(cell, dict):
                val = cell.get("value")
            else:
                val = cell
            if val is None:
                line.append("")
                gaps.append((row_zone, cls, col, "claude did not extract"))
            else:
                line.append(val)
        writer.writerow(line)
        extracted += 1
    return out.getvalue(), gaps if extracted else [
        ("(global)", "(any)", "(any)", "Claude returned no rows")
    ]


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        classify = event.get("classify") or event
        s3_key = classify.get("s3_key") or event.get("s3_key") or ""
        if not s3_key:
            raise RuntimeError("llm-extractor: no s3_key in input")
        out_s3_key = event.get("out_s3_key") or _default_out_key(s3_key)

        import boto3

        s3 = boto3.client("s3")
        pdf_bytes = s3.get_object(Bucket=INPUTS_BUCKET, Key=s3_key)[
            "Body"
        ].read()
        logger.info("llm-extractor: downloaded %d bytes from %s", len(pdf_bytes), s3_key)

        doc_type = (classify.get("doc_type") or "").lower()
        local = classify.get("local")
        logger.info(
            "llm-extractor: invoking Bedrock with doc_type=%s local=%s for key=%s",
            doc_type or "(none, rate_notice prompt)",
            local,
            s3_key,
        )
        payload = _invoke_bedrock(pdf_bytes, doc_type=doc_type, local=local)
        if payload.get("_parse_error"):
            logger.warning(
                "llm-extractor: Claude returned malformed JSON — see _raw"
            )

        csv_text, gaps = _to_canonical_csv(payload, classify)
        s3.put_object(
            Bucket=OUTPUTS_BUCKET,
            Key=out_s3_key,
            Body=csv_text.encode("utf-8"),
            ContentType="text/csv",
            ServerSideEncryption="aws:kms",
        )

        rows = payload.get("rows") or []
        result = {
            "s3_key": out_s3_key,
            "rows": len(rows),
            "extracted_rows": len(rows),
            "gaps": gaps,
            "gap_count": len(gaps),
            "checksum": None,
            "method": "llm_claude",
            "union_local": payload.get("union_local") or classify.get("local"),
        }
        logger.info("llm-extractor: %s", json.dumps({k: v for k, v in result.items() if k != "gaps"}))
        return result
    except Exception:
        logger.exception("llm-extractor failed")
        raise


def _default_out_key(input_pdf_key: str) -> str:
    """Output CSV key. We used to write ``<prefix>/output.csv`` which
    COLLIDED when multiple PDFs landed under the same batch+period directory
    (Rate Notice + CBA in the same batch each wrote output.csv, second writer
    wins). Per-source-PDF naming keeps every extraction's CSV addressable for
    audit + lets Publisher invocations reach the right CSV every time."""
    if "/" in input_pdf_key:
        prefix, base = input_pdf_key.rsplit("/", 1)
        stem = base.rsplit(".", 1)[0] if "." in base else base
        return f"{prefix}/{stem}.csv"
    stem = input_pdf_key.rsplit(".", 1)[0] if "." in input_pdf_key else input_pdf_key
    return f"{stem}.csv"
