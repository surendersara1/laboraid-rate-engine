"""ImproverAgent — applies reviewer corrections into a new rate-sheet version.

ONE flow (Phase-2 decision 2). On "Improve" the API records an `improvement_run`
and invokes this AgentCore runtime with {local, period, run_id}. The agent then,
in a SINGLE pass over ALL open corrections for the sheet (any mix/count of
overrides + comments):

  - OVERRIDE  -> apply the human value verbatim; if it's a base Wage, recompute the
                derived columns deterministically via the shared rate_math core.
  - COMMENT   -> re-synthesize that cell with the LLM (Bedrock), steered by the
                comment + profile + source PDFs; emit corrected value + provenance.
  - untouched -> carried over byte-identical.

It then writes ONE new version (v+1) of the rate sheet, records a per-cell change
log (improvement_changes), marks the corrections applied, and finishes the run.
Dual-control is untouched: the new version is `pending_review` — a human approves.

rate_math is the SAME module the synthesizer uses, so derived values are identical
to how the original was produced.
"""

from __future__ import annotations

import io
import json
import os
import uuid
from decimal import Decimal
from typing import Any

import boto3

import rate_math

MODEL_ID = os.environ.get("SYNTH_MODEL_ID", "us.anthropic.claude-opus-4-5-20251101-v1:0")
GUARDRAIL_ID = os.environ.get("BEDROCK_GUARDRAIL_ID", "")
INPUTS_BUCKET = os.environ.get("INPUTS_BUCKET", "laboraid-dev-l3-bucket-inputs")
DB = {
    "resourceArn": os.environ.get("AURORA_CLUSTER_ARN", ""),
    "secretArn": os.environ.get("AURORA_SECRET_ARN", ""),
    "database": "laboraid",
}

_rds = boto3.client("rds-data")
_s3 = boto3.client("s3")
_bedrock = boto3.client("bedrock-runtime")


# --- small rds-data helpers -------------------------------------------------
def _exec(sql: str, params: list[dict[str, Any]] | None = None) -> list[list[dict[str, Any]]]:
    return _rds.execute_statement(**DB, sql=sql, parameters=params or []).get("records", [])


def _s(v: str | None) -> dict[str, Any]:
    return {"isNull": True} if v is None else {"stringValue": v}


def _val(field: dict[str, Any]) -> Any:
    if field.get("isNull"):
        return None
    for k in ("stringValue", "longValue", "doubleValue", "booleanValue"):
        if k in field:
            return field[k]
    return None


# --- load -------------------------------------------------------------------
def _load_period(local: str, period: str) -> dict[str, Any] | None:
    rows = _exec(
        "SELECT rp.id::text, rp.version, rp.union_id::text, rp.source_files::text "
        "  FROM rate_periods rp JOIN unions u ON u.id = rp.union_id "
        " WHERE u.local = :l::int AND to_char(rp.start_date,'YYYY-MM-DD') = :p "
        " ORDER BY rp.version DESC LIMIT 1",
        [{"name": "l", "value": _s(local)}, {"name": "p", "value": _s(period)}],
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "period_id": _val(r[0]),
        "version": int(_val(r[1]) or 1),
        "union_id": _val(r[2]),
        "source_files": json.loads(_val(r[3]) or "{}"),
    }


def _load_profile(local: str) -> dict[str, Any]:
    rows = _exec(
        "SELECT profile_yaml::text FROM unions WHERE local = :l::int AND profile_yaml IS NOT NULL",
        [{"name": "l", "value": _s(local)}],
    )
    return json.loads(_val(rows[0][0])) if rows else {}


def _load_open_corrections(local: str, period: str) -> list[dict[str, Any]]:
    rows = _exec(
        "SELECT id::text, cell_id::text, kind, new_value, reason, package, column_name, zone "
        "  FROM cell_corrections "
        " WHERE union_local = :l AND period = :p AND status = 'open' "
        " ORDER BY created_at",
        [{"name": "l", "value": _s(local)}, {"name": "p", "value": _s(period)}],
    )
    return [
        {
            "id": _val(r[0]), "cell_id": _val(r[1]), "kind": _val(r[2]),
            "new_value": _val(r[3]), "reason": _val(r[4]),
            "package": _val(r[5]), "column_name": _val(r[6]), "zone": _val(r[7]),
        }
        for r in rows
    ]


def _load_cells(period_id: str) -> list[dict[str, Any]]:
    rows = _exec(
        "SELECT id::text, zone, package, dimensions::text, column_name, value::text, "
        "       value_type, provenance::text, confidence "
        "  FROM rate_cells WHERE period_id = :pid::uuid",
        [{"name": "pid", "value": _s(period_id)}],
    )
    return [
        {
            "id": _val(r[0]), "zone": _val(r[1]), "package": _val(r[2]),
            "dimensions": _val(r[3]) or "{}", "column_name": _val(r[4]),
            "value": _val(r[5]), "value_type": _val(r[6]) or "currency",
            "provenance": _val(r[7]) or "{}", "confidence": _val(r[8]),
        }
        for r in rows
    ]


# --- comment re-synthesis (LLM) --------------------------------------------
def _source_text(source_files: dict[str, Any], limit: int = 60000) -> str:
    """Concatenate text from the period's source PDFs (best-effort, budgeted)."""
    from pypdf import PdfReader

    keys: list[str] = []
    for v in (source_files or {}).get("uploads", []) or []:
        if isinstance(v, str):
            keys.append(v)
        elif isinstance(v, dict) and v.get("s3_key"):
            keys.append(v["s3_key"])
    out: list[str] = []
    size = 0
    for k in keys:
        try:
            body = _s3.get_object(Bucket=INPUTS_BUCKET, Key=k)["Body"].read()
            for page in PdfReader(io.BytesIO(body)).pages:
                t = page.extract_text() or ""
                out.append(t)
                size += len(t)
                if size >= limit:
                    return "\n".join(out)[:limit]
        except Exception:
            continue
    return "\n".join(out)[:limit]


def _resynthesize(cell: dict[str, Any], comment: str, profile: dict[str, Any], src: str) -> dict[str, Any]:
    """Re-extract one commented cell from the sources, steered by the comment.
    Returns {value: float|None, provenance: str, confidence: float}. Cites the
    source; returns null (a flagged gap) rather than fabricating."""
    prompt = (
        "You are correcting ONE cell of a union rate sheet. A human reviewer flagged it.\n"
        f"Cell: classification='{cell['package']}', column='{cell['column_name']}', "
        f"zone='{cell.get('zone')}', current value={cell['value']}.\n"
        f"Reviewer comment: \"{comment}\"\n"
        "Re-read the SOURCE TEXT below and determine the correct value for THIS cell only. "
        "Use the reviewer's comment as guidance. If the source does not support a value, "
        "return null (do not invent one).\n"
        "Respond with STRICT JSON: {\"value\": <number or null>, "
        "\"provenance\": \"<short cite of where in the source>\", \"confidence\": <0..1>}.\n\n"
        f"SOURCE TEXT:\n{src}"
    )
    kw: dict[str, Any] = {
        "modelId": MODEL_ID,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": 400, "temperature": 0},
    }
    if GUARDRAIL_ID:
        kw["guardrailConfig"] = {"guardrailIdentifier": GUARDRAIL_ID, "guardrailVersion": "DRAFT"}
    resp = _bedrock.converse(**kw)
    text = resp["output"]["message"]["content"][0]["text"]
    start, end = text.find("{"), text.rfind("}")
    parsed = json.loads(text[start : end + 1]) if start >= 0 else {}
    return {
        "value": parsed.get("value"),
        "provenance": str(parsed.get("provenance") or "re-synthesized from source"),
        "confidence": float(parsed.get("confidence") or 0.0),
    }


# --- process all corrections in ONE pass ------------------------------------
def _process(corrections, cells, profile, source_files) -> tuple[dict[str, dict], list[dict]]:
    """Returns (new_values_by_cell_id, change_log). Overrides deterministic +
    derived recompute; comments via LLM. Untouched cells absent from the map."""
    by_id = {c["id"]: c for c in cells}
    mults = (profile.get("wage") or {}).get("derived_multipliers") or {}
    new_values: dict[str, dict] = {}   # cell_id -> {value, source, provenance, confidence}
    changes: list[dict] = []
    src_text: str | None = None

    def _set(cid: str, value, source: str, prov: str, conf: float) -> None:
        cell = by_id.get(cid)
        prior = cell["value"] if cell else None
        new_values[cid] = {"value": value, "source": source, "provenance": prov, "confidence": conf}
        changes.append({
            "cell_id": cid, "package": cell["package"] if cell else None,
            "column_name": cell["column_name"] if cell else None,
            "prior_value": prior, "new_value": None if value is None else str(value),
            "source": source, "provenance": prov, "confidence": conf,
        })

    for corr in corrections:
        cid = corr["cell_id"]
        cell = by_id.get(cid)
        if not cell:
            continue
        if corr["kind"] == "override":
            val = float(corr["new_value"])
            _set(cid, val, "override", f"human override by reviewer: {corr.get('reason') or ''}".strip(), 1.0)
            # recompute derived siblings if this is a base Wage
            if cell["column_name"] == "Wage" and mults:
                derived = rate_math.recompute_derived(val, mults)
                for sib in cells:
                    if (sib["package"] == cell["package"] and sib["dimensions"] == cell["dimensions"]
                            and sib["column_name"] in derived):
                        _set(sib["id"], float(derived[sib["column_name"]]), "recompute",
                             f"recomputed from override of {cell['package']} Wage", 1.0)
        elif corr["kind"] == "comment":
            if src_text is None:
                src_text = _source_text(source_files)
            r = _resynthesize(cell, corr.get("reason") or "", profile, src_text)
            _set(cid, r["value"], "resynth", r["provenance"], r["confidence"])
            if cell["column_name"] == "Wage" and r["value"] is not None and mults:
                derived = rate_math.recompute_derived(r["value"], mults)
                for sib in cells:
                    if (sib["package"] == cell["package"] and sib["dimensions"] == cell["dimensions"]
                            and sib["column_name"] in derived):
                        _set(sib["id"], float(derived[sib["column_name"]]), "recompute",
                             f"recomputed from re-synthesized {cell['package']} Wage", 1.0)
    return new_values, changes


# --- write the new version (v+1) -------------------------------------------
def _write_new_version(period, cells, new_values, changes, run_id, local, period_str) -> int:
    old_pid = period["period_id"]
    new_pid = str(uuid.uuid4())
    to_version = period["version"] + 1
    _exec(
        "INSERT INTO rate_periods (id, union_id, start_date, end_date, status, "
        "  approval_state, canonical_json, source_files, version, parent_version) "
        "SELECT :nid::uuid, union_id, start_date, end_date, status, 'pending_review', "
        "  canonical_json, source_files, :ver, version "
        "  FROM rate_periods WHERE id = :oid::uuid",
        [{"name": "nid", "value": _s(new_pid)}, {"name": "ver", "value": {"longValue": to_version}},
         {"name": "oid", "value": _s(old_pid)}],
    )
    # Copy every cell into the new period, applying corrections; assign new cell ids.
    for c in cells:
        nv = new_values.get(c["id"])
        value = nv["value"] if nv else c["value"]
        prov = c["provenance"]
        conf = c["confidence"]
        if nv:
            prov = json.dumps({"method": nv["source"], "detail": nv["provenance"]})
            conf = nv["confidence"]
        new_cell_id = str(uuid.uuid4())
        _exec(
            "INSERT INTO rate_cells (id, period_id, zone, package, dimensions, column_name, "
            "  value, value_type, provenance, confidence) "
            "VALUES (:id::uuid, :pid::uuid, :zone, :pkg, :dim::jsonb, :col, "
            "  :val::numeric, :vt, :prov::jsonb, :conf::numeric)",
            [
                {"name": "id", "value": _s(new_cell_id)},
                {"name": "pid", "value": _s(new_pid)},
                {"name": "zone", "value": _s(c["zone"])},
                {"name": "pkg", "value": _s(c["package"])},
                {"name": "dim", "value": _s(c["dimensions"])},
                {"name": "col", "value": _s(c["column_name"])},
                {"name": "val", "value": ({"isNull": True} if value is None else {"stringValue": str(value)})},
                {"name": "vt", "value": _s(c["value_type"])},
                {"name": "prov", "value": _s(prov)},
                {"name": "conf", "value": ({"isNull": True} if conf is None else {"stringValue": str(conf)})},
            ],
        )
    # Record the change log against the run.
    for ch in changes:
        _exec(
            "INSERT INTO improvement_changes (id, run_id, cell_id, package, column_name, "
            "  prior_value, new_value, source, provenance, confidence) "
            "VALUES (:id::uuid, :run::uuid, :cid::uuid, :pkg, :col, :prior, :new, :src, :prov, :conf::numeric)",
            [
                {"name": "id", "value": _s(str(uuid.uuid4()))},
                {"name": "run", "value": _s(run_id)},
                {"name": "cid", "value": _s(ch["cell_id"])},
                {"name": "pkg", "value": _s(ch["package"])},
                {"name": "col", "value": _s(ch["column_name"])},
                {"name": "prior", "value": _s(None if ch["prior_value"] is None else str(ch["prior_value"]))},
                {"name": "new", "value": _s(ch["new_value"])},
                {"name": "src", "value": _s(ch["source"])},
                {"name": "prov", "value": _s(ch["provenance"])},
                {"name": "conf", "value": ({"isNull": True} if ch["confidence"] is None else {"stringValue": str(ch["confidence"])})},
            ],
        )
    return to_version


def improve(local: str, period: str, run_id: str) -> dict[str, Any]:
    period_row = _load_period(local, period)
    if not period_row:
        raise ValueError(f"no rate period for local={local} period={period}")
    corrections = _load_open_corrections(local, period)
    cells = _load_cells(period_row["period_id"])
    profile = _load_profile(local)

    new_values, changes = _process(corrections, cells, profile, period_row["source_files"])
    to_version = _write_new_version(period_row, cells, new_values, changes, run_id, local, period)

    # Mark the corrections applied + finish the run.
    _exec(
        "UPDATE cell_corrections SET status='applied' "
        " WHERE union_local=:l AND period=:p AND status='open'",
        [{"name": "l", "value": _s(local)}, {"name": "p", "value": _s(period)}],
    )
    n_over = sum(1 for c in corrections if c["kind"] == "override")
    n_comm = sum(1 for c in corrections if c["kind"] == "comment")
    summary = f"Applied {n_over} override(s) + {n_comm} comment(s) -> v{to_version}; {len(changes)} cells changed."
    _exec(
        "UPDATE improvement_runs SET status='succeeded', to_version=:v, finished_at=NOW(), summary=:s "
        " WHERE id=:id::uuid",
        [{"name": "v", "value": {"longValue": to_version}}, {"name": "s", "value": _s(summary)},
         {"name": "id", "value": _s(run_id)}],
    )
    return {"run_id": run_id, "to_version": to_version, "changed": len(changes), "summary": summary}


# --- AgentCore Runtime entrypoint ------------------------------------------
try:  # pragma: no cover - only in the deployed container
    from bedrock_agentcore.runtime import BedrockAgentCoreApp  # type: ignore[import-not-found]

    app = BedrockAgentCoreApp()

    @app.entrypoint  # type: ignore[misc]
    def invoke(payload: dict[str, Any]) -> Any:
        run_id = payload["run_id"]
        try:
            return improve(str(payload["local"]), str(payload["period"]), run_id)
        except Exception as e:  # mark the run failed so the UI can show it
            try:
                _exec(
                    "UPDATE improvement_runs SET status='failed', finished_at=NOW(), error=:e "
                    " WHERE id=:id::uuid",
                    [{"name": "e", "value": _s(str(e)[:1000])}, {"name": "id", "value": _s(run_id)}],
                )
            except Exception:
                pass
            raise

    app.run()
except ImportError:  # pragma: no cover - local/unit-test without AgentCore SDK
    pass
