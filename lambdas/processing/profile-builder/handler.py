"""LLM profile-builder — onboard a union from its CBA.

Reads a union's CBA (and optionally a sample rate notice) and extracts the
union's rate-sheet STRUCTURE — classifications, zones, fringe funds, indenture
cohorts, and overtime multipliers — as a profile saved to Aurora
(unions.profile_yaml). STRUCTURE ONLY, never dollar values: the synthesizer
later fills the numbers by extracting the rate notices. This is what makes the
system scale to any union without code changes.

Input:  {local, trade, docs: [{s3_key, filename}, ...]}
Output: {local, trade, saved: true, packages, zones, funds, has_cohorts}

Reuses the Bedrock+S3 role; needs RDS Data API to write the profile.
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

import master_data

try:  # pragma: no cover
    from aws_lambda_powertools import Logger, Tracer

    logger = Logger(service="laboraid-profile-builder")
    tracer = Tracer()

    def _instrument(fn: Any) -> Any:
        return logger.inject_lambda_context(tracer.capture_lambda_handler(fn))

except ModuleNotFoundError:  # pragma: no cover
    import logging

    logger = logging.getLogger("laboraid-profile-builder")

    def _instrument(fn: Any) -> Any:
        return fn


INPUTS_BUCKET = os.environ.get("INPUTS_BUCKET", "laboraid-dev-l3-bucket-inputs")
GUARDRAIL_ID = os.environ.get("BEDROCK_GUARDRAIL_ID", "")
MODEL_ID = os.environ.get("SYNTH_MODEL_ID", "us.anthropic.claude-opus-4-5-20251101-v1:0")
AURORA_CLUSTER_ARN = os.environ.get("AURORA_CLUSTER_ARN", "")
AURORA_SECRET_ARN = os.environ.get("AURORA_SECRET_ARN", "")

_META_COLS = ["Union Group", "Trade", "Union Local", "Zone",
              "Indentured Date is Before", "Indentured Date is After",
              "Package", "Start Date", "End Date"]

_OBJECTIVE = """You are onboarding a union local into a rate-sheet system. From the
attached collective bargaining agreement (CBA) — and any rate notice — extract
the union's rate-sheet STRUCTURE. STRUCTURE ONLY: which rows and columns the
rate sheet has. Do NOT extract or invent dollar amounts; the numbers are filled
later from the rate notices.

Produce, for Local {local} ({trade}):

1. ZONES — the distinct work zones / wage schedules the CBA defines (e.g.
   Building, Power & Gas, Industrial, Residential). If the local has only one,
   return a single zone (use "Building" if unnamed).

2. CLASSIFICATIONS (packages) — every worker classification: General Foreman,
   Foreman (and any variants like "Foreman - more than 4 men", "Area Foreman"),
   Journeyman, and the apprentice ladder. Name apprentices "Apprentice Year N"
   or "Apprentice Class N" exactly as the CBA labels them (including half-year
   splits like "Apprentice Year 2-A" / "2-B" if present).

3. INDENTURE COHORTS — if the CBA splits apprentices by indenture date (e.g.
   "indentured after 7/1/2024" vs "between 7/1/2020 and 6/30/2024"), list each
   cohort's date window {before, after}. Otherwise return [].

4. ROW TEMPLATE — the full set of rate-sheet rows: one entry per
   (zone x classification x cohort). For non-cohort classifications set
   indentured_before/after to null. Apprentice rows repeat once per cohort.

5. FUND COLUMNS — every fringe fund the CBA contributes to (Health & Welfare,
   Pension, SIS, Apprenticeship Training, Industry Promotion, local funds named
   "<fund> <local>", etc.), in the order the CBA lists them. Mark percent-based
   funds (e.g. union dues at 3% of wage) with "percent": true.

6. DERIVED WAGE MULTIPLIERS — overtime / premium wage columns computed from the
   base wage, with their multiplier: e.g. {"Wage 1.5x": 1.5, "Wage 2.0x": 2.0,
   "Wage Differential": 1.15}. Include trade-specific ones the CBA defines
   (e.g. "Temporary Heat", "Wage 1.1x"). These are RULES (coefficients), not
   dollar values.

7. FOREMAN PREMIUMS — how Foreman / General Foreman wages relate to journeyman
   (e.g. "Foreman = Journeyman + $2.50", "General Foreman = Foreman + $2.00").

CANONICAL NAMING — CRITICAL: every fund column name and package name you output
MUST be the canonical name from the lists below, NOT your own descriptive label.
Map each CBA fund to the closest canonical entry (e.g. the CBA's "supplemental
pension" -> "SIS"; "apprentice training" -> "Apprenticeship Training"; a local
fund -> "<Canonical Fund> <local>"). For derived wage columns use exactly:
"Wage Differential", "Wage 1.1x", "Wage 1.5x", "Wage 2.0x", "Temporary Heat".
If a fund has no canonical match, keep the CBA's name and add it anyway.
{vocab}

Return ONE JSON object, no prose, no markdown:
{
  "zones": ["..."],
  "packages": ["General Foreman", "Foreman", "Journeyman", "Apprentice Year 1", ...],
  "has_cohorts": false,
  "cohorts": [{"before": "2024-06-30", "after": "2020-07-01"}],
  "row_template": [{"zone": "Building", "package": "Journeyman",
                    "indentured_before": null, "indentured_after": null}, ...],
  "fund_columns": [{"name": "Health & Welfare", "percent": false}, ...],
  "derived_multipliers": {"Wage 1.5x": 1.5, "Wage 2.0x": 2.0},
  "foreman_premiums": "Foreman = JM + $X; GF = Foreman + $Y",
  "notes": "1-2 sentences on the structure"
}"""


def _parse_json(text: str) -> dict[str, Any] | None:
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    if start < 0:
        return None
    depth = j = 0
    in_str = esc = False
    best = None
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            if depth == 0:
                j = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                chunk = text[j:i + 1]
                try:
                    obj = json.loads(chunk)
                    if "row_template" in obj or "packages" in obj:
                        best = obj
                except json.JSONDecodeError:
                    pass
    return best


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    local = str(event.get("local") or "")
    trade = event.get("trade") or ""
    docs = event.get("docs") or []
    if not local or not docs:
        raise ValueError("profile-builder: local and docs required")

    import boto3
    from botocore.config import Config

    s3 = boto3.client("s3")
    bedrock = boto3.client(
        "bedrock-runtime",
        config=Config(read_timeout=840, connect_timeout=10, retries={"max_attempts": 1}),
    )

    # Canonical vocabulary so the builder maps to the client's names, not its own.
    try:
        funds = master_data.funds_for_union(local)
        fund_names = sorted({f.get("Fund Name") for f in funds if f.get("Fund Name")})
        pkgs = sorted({p.get("Package Name") for p in master_data.packages_all() if p.get("Package Name")})
        vocab = (
            "\nCANONICAL FUND NAMES:\n- " + "\n- ".join(fund_names) +
            "\n\nCANONICAL PACKAGE NAMES:\n- " + "\n- ".join(pkgs)
        )
    except Exception:
        vocab = ""
    system_prompt = (
        _OBJECTIVE.replace("{local}", str(local))
        .replace("{trade}", str(trade))
        .replace("{vocab}", vocab)
    )
    content: list[dict[str, Any]] = [{
        "type": "text",
        "text": (f"Onboard Local {local} ({trade}). {len(docs)} document(s) follow "
                 "(CBA + any rate notice). Extract the rate-sheet structure per the objective."),
    }]
    for d in docs:
        key = d.get("s3_key") or ""
        if not key:
            continue
        try:
            pdf = s3.get_object(Bucket=INPUTS_BUCKET, Key=key)["Body"].read()
        except Exception:
            logger.warning("profile-builder: could not fetch %s", key)
            continue
        # Bedrock caps a request at 100 PDF pages total. The structure (classes,
        # zones, funds, OT rules, apprentice ladder) lives in the CBA's early
        # articles, so cap to the first 100 pages for oversized agreements.
        from pdf_utils import first_pages, page_count

        capped = first_pages(pdf, 100)
        note = "" if page_count(pdf) <= 100 else " (first 100 pages of a longer CBA)"
        content.append({"type": "text", "text": f"\n===== {d.get('filename') or key}{note} =====\n"})
        content.append({"type": "document", "source": {
            "type": "base64", "media_type": "application/pdf",
            "data": base64.b64encode(capped).decode()}})

    body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 32000,
        "system": [{"type": "text", "text": system_prompt}],
        "messages": [{"role": "user", "content": content}],
    }
    kwargs: dict[str, Any] = {"modelId": MODEL_ID, "body": json.dumps(body)}
    if GUARDRAIL_ID:
        kwargs["guardrailIdentifier"] = GUARDRAIL_ID
        kwargs["guardrailVersion"] = "DRAFT"

    logger.info("profile-builder: invoking %s for local=%s with %d docs", MODEL_ID, local, len(docs))
    resp = bedrock.invoke_model(**kwargs)
    payload = json.loads(resp["body"].read())
    text = "\n".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text")
    built = _parse_json(text)
    if not built:
        raise ValueError("profile-builder: could not parse model JSON")

    # Assemble the full profile in the synthesizer's expected shape.
    funds = built.get("fund_columns") or []
    mults = built.get("derived_multipliers") or {}
    wage_block = ["Wage"] + [m for m in ["Wage Differential", "Wage 1.1x", "Wage 1.5x", "Wage 2.0x"]
                             if m in mults] + [m for m in mults if m not in
                             ("Wage Differential", "Wage 1.1x", "Wage 1.5x", "Wage 2.0x")]
    has_cohorts = bool(built.get("has_cohorts"))
    meta = ["Union Group", "Trade", "Union Local", "Zone"]
    if has_cohorts:
        meta += ["Indentured Date is Before", "Indentured Date is After"]
    meta += ["Package", "Start Date", "End Date"]
    column_order = meta + wage_block + [f["name"] for f in funds]

    profile = {
        "trade": trade,
        "local": str(local),
        "union_group": event.get("union_group") or "UA",
        "period_start": "",
        "period_end": "",
        "row_template": built.get("row_template") or [],
        "has_cohorts": has_cohorts,
        "cohorts": built.get("cohorts") or [],
        "zones": built.get("zones") or [],
        "packages": built.get("packages") or [],
        "wage": {"base": "Wage", "derived_multipliers": mults},
        "fund_columns": funds,
        "column_order": column_order,
        "foreman_premiums": built.get("foreman_premiums"),
        "built_from": [d.get("filename") for d in docs],
        "build_method": "llm_cba",
        "notes": built.get("notes"),
    }

    # Save to Aurora (system of record). Upsert the union row if new.
    if AURORA_CLUSTER_ARN and AURORA_SECRET_ARN:
        rds = boto3.client("rds-data")
        common = dict(resourceArn=AURORA_CLUSTER_ARN, secretArn=AURORA_SECRET_ARN, database="laboraid")
        import uuid
        rds.execute_statement(**common,
            sql=("INSERT INTO unions (id, local, trade, parent_intl) "
                 "VALUES (:id::uuid, :local::int, :trade, :parent) ON CONFLICT (local) DO NOTHING"),
            parameters=[
                {"name": "id", "value": {"stringValue": str(uuid.uuid4())}},
                {"name": "local", "value": {"stringValue": str(local)}},
                {"name": "trade", "value": {"stringValue": trade}},
                {"name": "parent", "value": {"stringValue": profile["union_group"]}},
            ])
        rds.execute_statement(**common,
            sql="UPDATE unions SET profile_yaml = :p::jsonb, profile_version = :v WHERE local = :local::int",
            parameters=[
                {"name": "p", "value": {"stringValue": json.dumps(profile)}},
                {"name": "v", "value": {"stringValue": "llm-built"}},
                {"name": "local", "value": {"stringValue": str(local)}},
            ])
        logger.info("profile-builder: saved profile for local=%s to Aurora", local)

    return {
        "local": local, "trade": trade, "saved": bool(AURORA_CLUSTER_ARN),
        "packages": len(profile["packages"]), "zones": profile["zones"],
        "funds": len(profile["fund_columns"]), "has_cohorts": has_cohorts,
        "rows": len(profile["row_template"]), "multipliers": list(mults.keys()),
    }
