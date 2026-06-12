"""Objective-driven rate-sheet SYNTHESIZER.

Replaces the per-doc-extract + mechanical-merge approach (which curve-fit
deterministic precedence/dedupe rules to cases we'd already seen) with a single
holistic LLM reasoning pass: ALL documents for one rate period are given to the
model together, with the OBJECTIVE and the role of each document, and the model
produces the finished canonical rate sheet in one shot. The model reasons about
precedence (newer rate notice supersedes the CBA), indenture cohorts, fund
naming, and overtime formulas — instead of us stitching independently-extracted
docs with brittle rules.

Input (from the batch planner):
  {
    "local": "281", "trade": "Sprinkler", "period": "2026-01-01",
    "docs": [ {s3_key, filename, doc_type, effective_date}, ... ]   # ordered
  }

Output:
  {
    "local", "period", "columns": [...],
    "rows": [ {zone, package, indentured_before, indentured_after,
               cells: {<fund>: <number|null>}}, ... ],
    "gaps": [ {zone, package, column, reason}, ... ],
    "notes": "<model's reasoning summary>"
  }
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

    logger = Logger(service="laboraid-synthesizer")
    tracer = Tracer()

    def _instrument(fn: Any) -> Any:
        return logger.inject_lambda_context(tracer.capture_lambda_handler(fn))

except ModuleNotFoundError:  # pragma: no cover
    import logging

    logger = logging.getLogger("laboraid-synthesizer")

    def _instrument(fn: Any) -> Any:
        return fn


INPUTS_BUCKET = os.environ.get("INPUTS_BUCKET", "laboraid-dev-l3-bucket-inputs")
OUTPUTS_BUCKET = os.environ.get("OUTPUTS_BUCKET", "laboraid-dev-l3-bucket-outputs")
GUARDRAIL_ID = os.environ.get("BEDROCK_GUARDRAIL_ID", "")
MODEL_ID = os.environ.get("SYNTH_MODEL_ID", "us.anthropic.claude-sonnet-4-6")


_OBJECTIVE = """You are the rate-sheet synthesizer for LaborAid. You produce the single
official rate sheet for one union local and one effective date by reasoning
over ALL of its source documents together.

THE OBJECTIVE
Produce the complete, correct rate sheet for Local {local} ({trade}),
effective {period}. Attached are every source document for this period, each
labelled with its type. Reason over them TOGETHER — do not treat them
independently.

THE ROLE OF EACH DOCUMENT TYPE
- CBA (collective bargaining agreement): defines STRUCTURE — the list of
  classifications/packages, the zones, the fund list, the overtime multipliers,
  the Foreman/General-Foreman premiums (e.g. "Foreman = Journeyman + $2.50" or
  a percentage), the apprentice progression (year/class ladder and % of
  journeyman), and any indenture-cohort rules. The CBA is the BASE STRUCTURE.
  It is NOT the source of the current dollar values for {period} unless no
  rate notice / wage sheet provides them.
- RATE NOTICE / WAGE SHEET (rate_notice, rate_sheet): the AUTHORITATIVE current
  dollar values effective on a specific date. When several exist, the one
  effective ON OR BEFORE {period} with the LATEST date wins, and it SUPERSEDES
  any value in the CBA. These are the source of truth for wages and fringe
  amounts on {period}.
- APPRENTICE WAGE SHEET (apprentice_scale): apprentice wages, frequently split
  by INDENTURE COHORT (e.g. "indentured before 6/30/2024 & after 7/1/2020" vs
  "indentured after 7/1/2024"). Emit each cohort as a DISTINCT set of rows and
  set the indentured_before / indentured_after fields — do NOT bake the cohort
  into the package name.

EXTRACTION DISCIPLINE — the most common mistakes, avoid them:
- PER-ZONE WAGES: when a local has multiple zones (e.g. Building vs Power &
  Gas, or Building vs Industrial), EACH zone has its OWN wage scale. Extract the
  wage for the row's specific zone from that zone's table. NEVER copy one zone's
  wage to another zone's row. Foreman/GF premiums are added to the SAME-zone
  journeyman wage.
- APPRENTICE WAGES: an apprentice's wage is the percentage of the journeyman
  wage that the document's apprentice schedule states for that year/class. Use
  the EXACT percentage from the apprentice wage table / CBA schedule — do not
  assume a generic ladder. If the rate notice prints apprentice dollar amounts
  directly, use those verbatim.
- FUND PRECEDENCE: a fund amount printed in the latest rate notice SUPERSEDES
  the same fund in the CBA. Only fall back to the CBA's figure when no rate
  notice states that fund.

HOW TO RESOLVE CONFLICTS (precedence)
1. For any wage or fringe value on {period}: the latest rate notice / wage
   sheet effective on or before {period} wins over the CBA.
2. Structure (which packages, zones, funds, formulas) comes from the CBA, then
   is OVERRIDDEN by anything a rate notice / wage sheet states explicitly.
3. Apprentice dollar values come from the apprentice wage sheets; if those give
   percentages of journeyman, compute dollars from the journeyman wage on
   {period}.
4. Never emit two rows or two columns for the same real fund/classification —
   reconcile differently-named-but-identical items into ONE using the Master
   List names below.

THE TARGET SCHEMA (Master List — use these canonical names)
Every fund column header, package name, and zone MUST be a Master List entry
below. If a document uses a different label for the same thing, map it to the
Master List name. If a fund genuinely has no Master List entry, emit it under
its document label and add a gap note (the reviewer triages via Rule 10).

DERIVED COLUMNS — EXTRACT WHEN STATED, ELSE LEAVE TO CODE
Overtime/differential columns are named "Wage Differential" (the shift/premium
wage, NOT "Wage 1.15x"), "Wage 1.5x", "Wage 2.0x", etc.
- If a document STATES an explicit value for one of these for a classification
  (journeyman wage sheets often list the shift-differential wage directly),
  put that EXACT stated value in the row's cells. Do not recompute it.
- If a document does NOT state it (apprentice scales usually omit the
  differential), OMIT it from cells — downstream code derives it from the base
  Wage times the multiplier you report.
Either way, REPORT the multiplier the CBA states for each derived column in the
"multipliers" object (see output), e.g. {"Wage Differential": 1.15,
"Wage 1.5x": 1.5, "Wage 2.0x": 2.0}. Never emit a "Wage 1.15x" column name.

NEVER FABRICATE — BUT EMIT EXPLICIT ZEROS
Every numeric cell must trace to a document (or a documented formula). If a
fund genuinely does not apply to a classification (e.g. first-year apprentices
get $0 pension), emit 0 explicitly — do NOT leave it blank. Only use null (and
a gap note) when the value is truly UNKNOWN/absent from every document. A blank
that should be 0 is a defect; so is a guessed number.

OUTPUT — CRITICAL: your ENTIRE response must be exactly ONE JSON object and
nothing else. No analysis, no reasoning, no narration, no scratchpad/draft
object, no markdown fences, no text before or after. Start at '{' and end at
'}'. Do all reasoning silently. Return ONE JSON object:
{
  "columns": ["Wage", "<Master fund names...>"],
  "multipliers": {"Wage Differential": 1.15, "Wage 1.5x": 1.5, "Wage 2.0x": 2.0},
  "rows": [
    {
      "zone": "Building",
      "package": "Journeyman",
      "indentured_before": null,
      "indentured_after": null,
      "cells": {"Wage": 63.20, "Health & Welfare 281": 15.45, "Pension": 7.45, ...}
    },
    {
      "zone": "Building",
      "package": "Apprentice Year 5",
      "indentured_before": "2024-06-30",
      "indentured_after": "2020-07-01",
      "cells": {"Wage": 50.55, ...}
    }
  ],
  "gaps": [{"zone": "...", "package": "...", "column": "...", "reason": "..."}],
  "notes": "1-3 sentence summary of how you resolved precedence and cohorts"
}
Cells must be scalar numbers or null and must NOT include multiplier columns.
Percent values become decimals (6.00% -> 6.0). Emit rows in a sensible order
(Foreman/Journeyman first, then apprentices ascending, each cohort grouped)."""


def _render_objective(local: str | int, trade: str, period: str) -> str:
    """Fill the three named placeholders without touching the literal JSON
    braces in the example block (str.format would choke on them)."""
    return (
        _OBJECTIVE.replace("{local}", str(local))
        .replace("{trade}", str(trade))
        .replace("{period}", str(period))
    )


PROFILES_DIR = os.environ.get(
    "PROFILES_DIR",
    os.path.join(os.path.dirname(__file__), "profiles"),
)


AURORA_CLUSTER_ARN = os.environ.get("AURORA_CLUSTER_ARN", "")
AURORA_SECRET_ARN = os.environ.get("AURORA_SECRET_ARN", "")


def _load_profile_aurora(local: str | int) -> dict[str, Any] | None:
    """Load the union's profile from Aurora (unions.profile_yaml) — the system
    of record. Returns None if not configured / not found / unreadable, so the
    caller can fall back to the bundled copy."""
    if not (AURORA_CLUSTER_ARN and AURORA_SECRET_ARN):
        return None
    try:
        import boto3

        rds = boto3.client("rds-data")
        r = rds.execute_statement(
            resourceArn=AURORA_CLUSTER_ARN, secretArn=AURORA_SECRET_ARN, database="laboraid",
            sql="SELECT profile_yaml::text FROM unions WHERE local = :l::int AND profile_yaml IS NOT NULL",
            parameters=[{"name": "l", "value": {"stringValue": str(local)}}],
        )
        recs = r.get("records") or []
        if recs and not recs[0][0].get("isNull"):
            logger.info("synthesizer: loaded profile from AURORA for local=%s", local)
            return json.loads(recs[0][0]["stringValue"])
    except Exception:
        logger.warning("synthesizer: Aurora profile load failed for %s — falling back", local)
    return None


PROFILE_BUILDER_FN = os.environ.get("PROFILE_BUILDER_FN", "laboraid-dev-l4-fn-profile-builder")


def _ensure_profile(local: str | int, trade: str, docs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Auto-onboard: build the profile from this batch's CBA and save to Aurora,
    then return it. Runs only when no profile exists for the local."""
    cba_docs = [d for d in docs if (d.get("doc_type") == "cba")
                or "cba" in (d.get("filename") or "").lower()]
    if not cba_docs:
        cba_docs = docs  # no obvious CBA — let the builder read everything
    try:
        import boto3
        from botocore.config import Config

        lam = boto3.client("lambda", config=Config(read_timeout=890, connect_timeout=10,
                                                    retries={"max_attempts": 0}))
        logger.info("synthesizer: AUTO-ONBOARD local=%s — building profile from %d CBA doc(s)",
                    local, len(cba_docs))
        lam.invoke(
            FunctionName=PROFILE_BUILDER_FN,
            Payload=json.dumps({"local": str(local), "trade": trade,
                                "docs": [{"s3_key": d.get("s3_key"), "filename": d.get("filename")}
                                         for d in cba_docs]}).encode(),
        )
        return _load_profile_aurora(local)
    except Exception:
        logger.warning("synthesizer: auto-onboard failed for %s — using generic schema", local)
        return None


def _load_profile(trade: str, local: str | int) -> dict[str, Any] | None:
    """Load the per-union schema profile. AURORA is the system of record; the
    bundled JSON is a fallback so the pipeline still runs if Aurora is briefly
    unreachable. The profile is the EXACT target schema — column names,
    packages, cohorts, multipliers.

    Trade may be absent from the planner payload; fall back to matching any
    profile whose filename ends in ``_<local>.json``."""
    aurora = _load_profile_aurora(local)
    if aurora:
        return aurora
    candidates = []
    if trade:
        candidates.append(os.path.join(PROFILES_DIR, f"{str(trade).lower().replace(' ', '_')}_{local}.json"))
    # local-only fallback
    try:
        for fn in os.listdir(PROFILES_DIR):
            if fn.endswith(f"_{local}.json"):
                candidates.append(os.path.join(PROFILES_DIR, fn))
    except FileNotFoundError:
        pass
    for cand in candidates:
        try:
            with open(cand, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            continue
        except Exception:
            logger.warning("profile load failed for %s", cand)
            return None
    return None


def _profile_schema(profile: dict[str, Any]) -> str:
    """Render the frozen profile as the synthesizer's exact target schema."""
    funds = profile.get("fund_columns") or []
    fund_lines = [
        f"- {f.get('name')}" + ("  (PERCENT-based: emit as a decimal percent, e.g. 3.0 for 3%)"
                                 if f.get("percent") else "")
        for f in funds
    ]
    pkg_lines = [f"- {p}" for p in (profile.get("packages") or [])]
    zone_lines = [f"- {z}" for z in (profile.get("zones") or [])]
    mults = (profile.get("wage") or {}).get("derived_multipliers") or {}
    cohort_block = ""
    if profile.get("has_cohorts"):
        cohort_block = (
            "\n\nINDENTURE COHORTS — this local splits apprentices by indenture "
            "cohort. Emit each apprentice package ONCE PER COHORT below, setting "
            "indentured_before / indentured_after to the cohort's dates:\n"
            + "\n".join(
                f"- before={c.get('before') or 'none'}, after={c.get('after') or 'none'}"
                for c in (profile.get("cohorts") or [])
            )
            + "\nMatch each cohort to the apprentice wage sheet whose indenture "
            "window corresponds; a single sheet may carry more than one cohort."
        )
    # EXACT row template — the model fills values into THESE rows only.
    template = profile.get("row_template") or []
    tmpl_lines = []
    for i, t in enumerate(template):
        parts = [f"zone={t.get('zone') or '-'}", f"package={t.get('package')}"]
        if t.get("indentured_before") or t.get("indentured_after"):
            parts.append(f"indentured_before={t.get('indentured_before') or 'none'}")
            parts.append(f"indentured_after={t.get('indentured_after') or 'none'}")
        tmpl_lines.append(f"{i + 1}. " + ", ".join(parts))
    template_block = (
        "\n\nEXACT OUTPUT ROWS — produce ONE row object for EACH of the "
        f"{len(template)} rows below, in this order, and NO others. Copy the "
        "zone / package / indentured_* fields VERBATIM from here; your job is "
        "only to fill each row's `cells` with the fund values from the "
        "documents. Do NOT add, drop, merge, or rename rows:\n"
        + "\n".join(tmpl_lines)
    ) if template else ""

    return (
        f"FROZEN TARGET SCHEMA for Local {profile.get('local')} "
        f"({profile.get('trade')}) — use these EXACT canonical names; do not "
        "invent, rename, or substitute (e.g. use the package/fund label spelled "
        "below even if the source PDF spells it differently).\n\n"
        "FUND COLUMNS (exact names, in order) — fill each for every row:\n"
        + "\n".join(fund_lines) +
        (f"\n\nDERIVED WAGE MULTIPLIERS (code computes these from Wage — do NOT "
         f"put them in cells, just confirm in 'multipliers'): {mults}" if mults else "") +
        template_block +
        ("" if template else
         "\n\nCLASSIFICATION PACKAGES:\n" + "\n".join(pkg_lines) +
         "\n\nZONES:\n" + "\n".join(zone_lines) + cohort_block)
    )


def _master_schema(local: str | int) -> str:
    funds = master_data.funds_for_union(local)
    packages = master_data.packages_all()
    zones = master_data.zones_for_union(local)
    fund_lines = [
        f"- {f.get('Fund Name')} (type {f.get('Fund Type')}, "
        f"{f.get('Percentage Based Fund (Hourly, Percent, or Both)') or 'hourly'})"
        for f in funds
    ]
    pkg_lines = [f"- {p.get('Package Name')}" for p in packages]
    zone_lines = [f"- {z.get('Zone Name')}" for z in zones]
    return (
        "MASTER FUND LIST (canonical fund column names for Local "
        f"{local}):\n" + "\n".join(fund_lines) +
        "\n\nMASTER PACKAGE LIST (canonical classification names):\n" + "\n".join(pkg_lines) +
        "\n\nMASTER ZONE LIST:\n" + "\n".join(zone_lines)
    )


def _pii_safe_lines(layout: dict[str, Any]) -> str:
    """Compact, PII-light OCR hint from a Textract layout (LINES only)."""
    if not layout or layout.get("method") == "text_layer_present":
        return ""
    blocks = layout.get("Blocks") or []
    lines = [b.get("Text", "").strip() for b in blocks if b.get("BlockType") == "LINE"]
    lines = [l for l in lines if l][:500]
    return "\n".join(lines)


def _fetch_layout(s3: Any, s3_key: str) -> dict[str, Any]:
    try:
        body = s3.get_object(Bucket=OUTPUTS_BUCKET, Key=f"{s3_key}.layout.json")["Body"].read()
        return json.loads(body)
    except Exception:
        return {}


def _mult_round(wage: float, factor: float, places: int = 2) -> float:
    """Multiply wage*factor in Decimal space (avoids float error like
    50.55*1.5 == 75.8249999) and round half AWAY from zero (the client's
    convention: 32.7175 -> 32.72), not Python's bankers' rounding."""
    import decimal

    q = decimal.Decimal(1).scaleb(-places)
    product = decimal.Decimal(str(wage)) * decimal.Decimal(str(factor))
    return float(product.quantize(q, rounding=decimal.ROUND_HALF_UP))


def _date_key(v: Any) -> str:
    """Canonicalize a date to 'YYYY-MM-DD' for matching (handles M/D/YY, ISO)."""
    s = str(v or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if m:
        yr = m.group(3)
        yr = yr if len(yr) == 4 else f"20{yr}"
        return f"{yr}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return s


def _conform_to_template(result: dict[str, Any], profile: dict[str, Any]) -> None:
    """Force the output to be EXACTLY the profile's row template — one row per
    template entry, in order, with the model's extracted cells matched in by
    (package, cohort[, zone]). The model fills values; the template owns
    structure. This kills both missing and phantom rows."""
    template = profile.get("row_template") or []
    if not template:
        return
    model_rows = result.get("rows") or []

    def pk(r):
        return str(r.get("package") or "").strip().lower()

    def coh(r):
        return (_date_key(r.get("indentured_before")), _date_key(r.get("indentured_after")))

    def zn(r):
        return str(r.get("zone") or "").strip().lower()

    used: set[int] = set()

    def find(t, with_zone):
        for r in model_rows:
            if id(r) in used:
                continue
            if pk(r) == pk(t) and coh(r) == coh(t) and (not with_zone or zn(r) == zn(t)):
                return r
        return None

    out = []
    for t in template:
        mr = find(t, True) or find(t, False)
        if mr is not None:
            used.add(id(mr))
        out.append({
            "zone": t.get("zone"),
            "package": t.get("package"),
            "indentured_before": t.get("indentured_before"),
            "indentured_after": t.get("indentured_after"),
            "cells": dict((mr or {}).get("cells") or {}),
        })
    result["rows"] = out


def _apply_flat_funds(result: dict[str, Any], profile: dict[str, Any]) -> None:
    """Set every row's flat-fund cells to the union's single contractual rate
    from the profile. These funds don't vary by classification, so the profile
    value is authoritative — it corrects model misreads that would otherwise
    repeat on every row."""
    flat = profile.get("flat_funds") or {}
    if not flat:
        return
    for row in result.get("rows") or []:
        cells = row.get("cells")
        if not isinstance(cells, dict):
            cells = {}
            row["cells"] = cells
        for name, val in flat.items():
            cells[name] = val


def _apply_multipliers(result: dict[str, Any]) -> None:
    """Compute derived wage columns (Wage Differential, Wage 1.5x, ...) in code
    from each row's base Wage and the model-reported multipliers. Keeps the LLM
    out of arithmetic — it reasons about WHICH values apply; code does the math."""
    mults = result.get("multipliers") or {}
    if not isinstance(mults, dict) or not mults:
        return
    cols = list(result.get("columns") or [])
    for name_, factor in mults.items():
        try:
            float(factor)
        except (TypeError, ValueError):
            continue
        if name_ not in cols:
            # place differential/OT columns right after Wage
            insert_at = (cols.index("Wage") + 1) if "Wage" in cols else len(cols)
            cols.insert(insert_at, name_)
    result["columns"] = cols
    for row in result.get("rows") or []:
        cells = row.get("cells") or {}
        wage = cells.get("Wage")
        if wage is None:
            continue
        for name_, factor in mults.items():
            # Respect a value the model EXTRACTED from a document (stated
            # differential); only derive the ones it left blank.
            if cells.get(name_) is not None:
                continue
            try:
                cells[name_] = _mult_round(float(wage), float(factor), 2)
            except (TypeError, ValueError):
                continue
        row["cells"] = cells


def _parse_json_lenient(json_text: str) -> dict[str, Any] | None:
    """Parse model JSON, tolerating the usual LLM quirks: trailing commas,
    // and /* */ comments, NaN/Infinity, and stray '$'/',' in numbers."""
    attempts = [json_text]
    # strip line + block comments
    no_comments = re.sub(r"//[^\n\r]*", "", json_text)
    no_comments = re.sub(r"/\*.*?\*/", "", no_comments, flags=re.DOTALL)
    attempts.append(no_comments)
    # drop trailing commas
    attempts.append(re.sub(r",(\s*[}\]])", r"\1", no_comments))
    # quote bare $ amounts like : $63.20  ->  : 63.20
    attempts.append(re.sub(r":\s*\$([0-9.]+)", r": \1", attempts[-1]))
    for cand in attempts:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    return None


def _iter_balanced_objects(text: str):
    """Yield every balanced top-level {...} substring, honoring strings so a
    brace inside a JSON string value doesn't throw off the depth count."""
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth, j, in_str, esc = 0, i, False, False
        while j < n:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        yield text[i:j + 1]
                        break
            j += 1
        i = j + 1


def _best_object(text: str) -> dict[str, Any] | None:
    """Return the parseable top-level object with the most rows (the real
    answer, not an earlier scratchpad draft)."""
    best, best_rows = None, -1
    for chunk in _iter_balanced_objects(text):
        obj = _parse_json_lenient(chunk)
        if not isinstance(obj, dict):
            continue
        nrows = len(obj.get("rows") or []) if isinstance(obj.get("rows"), list) else -1
        if nrows > best_rows:
            best, best_rows = obj, nrows
    return best


def _extract_balanced_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return text
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


_META_COLS = ["Union Group", "Trade", "Union Local", "Zone",
              "Indentured Date is Before", "Indentured Date is After",
              "Package", "Start Date", "End Date"]


def _us_to_iso(us: str) -> str:
    """6/30/26 -> 2026-06-30 (for rate_periods.end_date). Pass ISO through."""
    s = (us or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if not m:
        return ""
    mo, da, yr = m.groups()
    yr = yr if len(yr) == 4 else f"20{yr}"
    return f"{yr}-{int(mo):02d}-{int(da):02d}"


def _fmt_date(iso_or_us: str) -> str:
    """2026-01-01 -> 1/1/26 (the client's M/D/YY); pass through M/D/YY."""
    s = (iso_or_us or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        return f"{int(m.group(2))}/{int(m.group(3))}/{m.group(1)[2:]}"
    return s


def _emit_client_csv(result: dict[str, Any], profile: dict[str, Any] | None,
                     local: str, trade: str, period: str) -> str:
    """Render the synthesized rows as the exact client-format CSV the publisher
    already consumes (kernel-CSV drop-in). Cohort -> Indentured columns;
    percent funds keep the '%'."""
    import csv as _csv
    import io as _io

    col_order = (profile or {}).get("column_order") or (
        _META_COLS + ["Wage"] + list((result.get("columns") or [])))
    pct = {f["name"] for f in (profile or {}).get("fund_columns", []) if f.get("percent")}
    union_group = (profile or {}).get("union_group", "UA")
    start = _fmt_date((profile or {}).get("period_start") or period)
    end = _fmt_date((profile or {}).get("period_end") or "")

    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(col_order)
    for row in result.get("rows") or []:
        cells = row.get("cells") or {}
        out = []
        for c in col_order:
            if c == "Union Group":
                out.append(union_group)
            elif c == "Trade":
                out.append(trade or (profile or {}).get("trade", ""))
            elif c == "Union Local":
                out.append(str(local))
            elif c == "Zone":
                out.append(row.get("zone", ""))
            elif c == "Indentured Date is Before":
                out.append(_fmt_date(row.get("indentured_before")))
            elif c == "Indentured Date is After":
                out.append(_fmt_date(row.get("indentured_after")))
            elif c == "Package":
                out.append(row.get("package", ""))
            elif c == "Start Date":
                out.append(start)
            elif c == "End Date":
                out.append(end)
            else:
                v = cells.get(c)
                if v is None:
                    out.append("")
                elif c in pct:
                    out.append(f"{float(v):.2f}%")
                else:
                    out.append(f"{float(v):g}" if isinstance(v, (int, float)) else str(v))
        w.writerow(out)
    return buf.getvalue()


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    local = str(event.get("local") or "")
    trade = event.get("trade") or ""
    period = event.get("period") or ""
    docs = event.get("docs") or []
    if not docs:
        raise ValueError("synthesizer: no docs")

    import boto3
    from botocore.config import Config

    s3 = boto3.client("s3")
    bedrock = boto3.client(
        "bedrock-runtime",
        config=Config(read_timeout=840, connect_timeout=10, retries={"max_attempts": 1}),
    )

    trace: list[dict[str, Any]] = []
    profile = _load_profile(trade, local)
    if profile:
        trace.append({"call": "Aurora", "detail": f"Loaded profile for Local {local} (unions.profile_yaml)"})
    if not profile:
        # AUTO-ONBOARD: no profile for this local yet — build one from the
        # batch's CBA, save to Aurora, then continue in the same pass.
        trace.append({"call": "Profile-builder", "detail": f"No profile for {local} — auto-onboarding from CBA"})
        profile = _ensure_profile(local, trade, docs)
    if profile and not trade:
        trade = profile.get("trade") or trade  # planner may omit trade
    if profile:
        schema_block = _profile_schema(profile)
        logger.info("synthesizer: using FROZEN PROFILE for %s %s", trade, local)
    else:
        schema_block = _master_schema(local)
        logger.info("synthesizer: no profile for %s %s — using master_data", trade, local)
    system_prompt = _render_objective(local, trade, period) + "\n\n" + schema_block

    # Build ONE user turn containing every document + its OCR hint, each
    # preceded by a label telling the model what role it plays.
    content: list[dict[str, Any]] = [{
        "type": "text",
        "text": (
            f"Synthesize the rate sheet for Local {local} ({trade}) effective {period}.\n"
            f"{len(docs)} source documents follow, each labelled with its type and "
            f"effective date. Reason over all of them together per the objective."
        ),
    }]
    # Bedrock caps a request at 100 PDF pages TOTAL. The dollar values live in
    # the rate notices / wage sheets; the CBA only carries STRUCTURE, which the
    # profile already encodes. So fetch all docs, fill the page budget with the
    # VALUE docs first, and drop the (large) CBA if it doesn't fit.
    from pdf_utils import first_pages, page_count

    fetched = []
    for d in docs:
        s3_key = d.get("s3_key") or ""
        if not s3_key:
            continue
        try:
            pdf = s3.get_object(Bucket=INPUTS_BUCKET, Key=s3_key)["Body"].read()
        except Exception:
            logger.warning("synthesizer: could not fetch %s", s3_key)
            continue
        fetched.append((d, pdf, page_count(pdf)))

    # The CBA carries STRUCTURE (Foreman/GF premiums, apprentice ladders,
    # overtime rules) the rate notices don't — it must NOT be dropped. Order:
    # CBA first (capped to 100pg if huge), then value docs LATEST-period first
    # so it's the OLD/irrelevant notices that fall off the page budget, never
    # the CBA or the current-period notice.
    cba = [t for t in fetched if (t[0].get("doc_type") or "").lower() == "cba"]
    val = sorted([t for t in fetched if (t[0].get("doc_type") or "").lower() != "cba"],
                 key=lambda t: str(t[0].get("effective_date") or ""), reverse=True)
    cba = [(d, first_pages(pdf, 100), min(pg, 100)) for d, pdf, pg in cba]
    fetched = cba + val

    budget = 100
    used = 0
    source_files: list[str] = []
    for d, pdf, pages in fetched:
        if used + min(pages, 100) > budget:
            logger.info("synthesizer: page budget full — skipping OLD doc %s (%d pages)",
                        d.get("filename"), pages)
            continue
        source_files.append(d.get("s3_key") or d.get("filename") or "")
        hint = _pii_safe_lines(_fetch_layout(s3, s3_key=d.get("s3_key")))
        label = (
            f"\n===== DOCUMENT: type={d.get('doc_type')} "
            f"effective_date={d.get('effective_date')} file={d.get('filename')} =====\n"
            + (f"(OCR text of this document:)\n{hint}\n" if hint else "")
        )
        content.append({"type": "text", "text": label})
        content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf",
                       "data": base64.b64encode(pdf).decode()},
        })
        used += pages

    body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 32000,
        "system": [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
    }
    kwargs: dict[str, Any] = {"modelId": MODEL_ID, "body": json.dumps(body)}
    if GUARDRAIL_ID:
        kwargs["guardrailIdentifier"] = GUARDRAIL_ID
        kwargs["guardrailVersion"] = "DRAFT"

    dropped = len(fetched) - len(source_files)
    trace.append({"call": "Documents",
                  "detail": f"Prepared {len(source_files)} PDF(s) for the model"
                            + (f"; dropped {dropped} older notice(s) to fit Bedrock's 100-page limit" if dropped else "")})
    trace.append({"call": "Bedrock · Claude Opus 4.5",
                  "detail": f"InvokeModel — reading {len(source_files)} document(s) against the profile"
                            + (f" (guardrail {GUARDRAIL_ID})" if GUARDRAIL_ID else "")})
    logger.info("synthesizer: invoking %s with %d docs for local=%s period=%s",
                MODEL_ID, len(docs), local, period)
    resp = bedrock.invoke_model(**kwargs)
    payload = json.loads(resp["body"].read())
    text = "\n".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text").strip()
    text = re.sub(r"```(?:json)?", "", text)
    # The model may "think out loud" and emit a scratchpad object before the
    # real answer (seen on multi-doc unions). Parse EVERY balanced top-level
    # object and keep the best one — the one with the most rows.
    result = _best_object(text)
    if result is None:
        # Capture the raw model output for diagnosis before failing.
        try:
            s3.put_object(
                Bucket=OUTPUTS_BUCKET,
                Key=f"synthesized/{event.get('batch_id') or local}.raw.txt",
                Body=text.encode("utf-8"), ContentType="text/plain",
            )
        except Exception:
            pass
        raise ValueError(f"synthesizer: could not parse model JSON (len={len(text)})")

    result.setdefault("columns", [])
    result.setdefault("rows", [])
    result.setdefault("gaps", [])
    if profile:
        # Frozen profile multipliers are authoritative; keep any extra ones the
        # model reported for columns the profile doesn't define.
        prof_mults = (profile.get("wage") or {}).get("derived_multipliers") or {}
        result["multipliers"] = {**(result.get("multipliers") or {}), **prof_mults}
        result["column_order"] = profile.get("column_order")
    _apply_multipliers(result)
    result["local"] = local
    result["period"] = period
    result["trade"] = trade
    result["start_date"] = period
    result["source_files"] = source_files  # PDFs the AI actually read (lineage)
    if profile:
        result["end_date"] = _us_to_iso(profile.get("period_end") or "")
        result["union_group"] = profile.get("union_group", "UA")
        result["percent_columns"] = [
            f["name"] for f in profile.get("fund_columns", []) if f.get("percent")
        ]
    trace.append({"call": "Parse + compute",
                  "detail": f"Extracted {len(result['rows'])} classifications; computed derived wage columns "
                            f"({len(result.get('multipliers') or {})} multipliers); {len(result['gaps'])} gap(s) flagged"})
    result["trace"] = trace
    logger.info("synthesizer: produced %d rows, %d gaps. notes=%s",
                len(result["rows"]), len(result["gaps"]), str(result.get("notes"))[:200])

    # Emit (a) the full JSON for audit and (b) the client-format CSV the
    # publisher consumes — same format as the kernel CSV it already handles.
    batch_id = event.get("batch_id") or f"{local}-{period}"
    output_key = f"synthesized/{batch_id}.json"
    csv_key = f"synthesized/{batch_id}.csv"
    csv_text = _emit_client_csv(result, profile, local, trade, period)
    s3.put_object(Bucket=OUTPUTS_BUCKET, Key=csv_key,
                  Body=csv_text.encode("utf-8"), ContentType="text/csv")
    # Excel twin of the canonical CSV (same rows/columns), numbers typed.
    xlsx_key = f"synthesized/{batch_id}.xlsx"
    try:
        import csv as _csv
        import io as _io

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = f"{local} {period}"
        for r_i, row in enumerate(_csv.reader(_io.StringIO(csv_text))):
            out_row = []
            for cell in row:
                v = (cell or "").strip()
                if r_i > 0 and v and not v.endswith("%"):
                    try:
                        out_row.append(float(v))
                        continue
                    except ValueError:
                        pass
                out_row.append(cell)
            ws.append(out_row)
        xbuf = _io.BytesIO()
        wb.save(xbuf)
        s3.put_object(Bucket=OUTPUTS_BUCKET, Key=xlsx_key, Body=xbuf.getvalue(),
                      ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        result["output_xlsx"] = xlsx_key
    except Exception:
        logger.warning("synthesizer: xlsx generation skipped")
        xlsx_key = ""
    # Write the full JSON LAST so it includes output_xlsx (synth-publish reads
    # this to record source_files.output_xlsx for the review page).
    s3.put_object(Bucket=OUTPUTS_BUCKET, Key=output_key,
                  Body=json.dumps(result).encode("utf-8"), ContentType="application/json")
    return {
        "local": local,
        "period": period,
        "trade": trade,
        "batch_id": event.get("batch_id"),
        # shape the publisher's `_publish` expects: canonical.s3_key -> the CSV.
        "canonical": {"s3_key": csv_key, "replace": True},
        "classify": {"local": local, "trade": trade, "s3_key": csv_key},
        "output_key": output_key,
        "csv_key": csv_key,
        "output_xlsx": xlsx_key,
        "row_count": len(result["rows"]),
        "gap_count": len(result["gaps"]),
        "trace": trace,
        "synthesized": True,
    }
