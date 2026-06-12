"""Synthesized-rate-sheet publisher.

Writes the SYNTHESIZER's structured output to Aurora as the authoritative rate
sheet for one (union, period) — a clean REPLACE, not a per-doc merge. Unlike the
legacy CSV publisher (which strips cohort columns), this preserves indenture
cohorts in ``rate_cells.dimensions`` so the review grid and export show the
client's exact 15-row format.

Input: the synthesizer's return (passed straight through by the SFN). It reads
the full rows from the synthesizer's JSON at ``output_key`` in OUTPUTS_BUCKET.

Reuses the publisher's IAM role (RDS Data API + S3) — no new IAM.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

try:  # pragma: no cover
    from aws_lambda_powertools import Logger, Tracer

    logger = Logger(service="laboraid-synth-publish")
    tracer = Tracer()

    def _instrument(fn: Any) -> Any:
        return logger.inject_lambda_context(tracer.capture_lambda_handler(fn))

except ModuleNotFoundError:  # pragma: no cover
    import logging

    logger = logging.getLogger("laboraid-synth-publish")

    def _instrument(fn: Any) -> Any:
        return fn


OUTPUTS_BUCKET = os.environ.get("OUTPUTS_BUCKET", "laboraid-dev-l3-bucket-outputs")
_COHORT_BEFORE = "Indentured Date is Before"
_COHORT_AFTER = "Indentured Date is After"


def _sv(v: str) -> dict[str, Any]:
    return {"stringValue": v}


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    import boto3

    rds = boto3.client("rds-data")
    s3 = boto3.client("s3")
    common = dict(
        resourceArn=os.environ["AURORA_CLUSTER_ARN"],
        secretArn=os.environ["AURORA_SECRET_ARN"],
        database="laboraid",
    )

    output_key = event.get("output_key") or (event.get("canonical") or {}).get("json_key")
    if not output_key:
        raise ValueError("synth-publish: no output_key in event")
    result = json.loads(s3.get_object(Bucket=OUTPUTS_BUCKET, Key=output_key)["Body"].read())

    local = str(result.get("local") or event.get("local") or "")
    trade = result.get("trade") or event.get("trade") or ""
    union_group = result.get("union_group") or "UA"
    start_date = result.get("start_date") or result.get("period") or ""
    end_date = result.get("end_date") or ""
    csv_key = event.get("csv_key") or (event.get("canonical") or {}).get("s3_key") or ""
    rows = result.get("rows") or []
    percent = set(result.get("percent_columns") or [])
    if not local or not start_date:
        raise ValueError(f"synth-publish: missing local/start_date (local={local!r} sd={start_date!r})")

    # 1) union upsert (race-safe).
    rds.execute_statement(
        **common,
        sql=("INSERT INTO unions (id, local, trade, parent_intl) "
             "VALUES (:id::uuid, :local::int, :trade, :parent) "
             "ON CONFLICT (local) DO NOTHING"),
        parameters=[
            {"name": "id", "value": _sv(str(uuid.uuid4()))},
            {"name": "local", "value": _sv(str(local))},
            {"name": "trade", "value": _sv(trade)},
            {"name": "parent", "value": _sv(union_group)},
        ],
    )
    uid = rds.execute_statement(
        **common, sql="SELECT id::text FROM unions WHERE local = :local::int",
        parameters=[{"name": "local", "value": _sv(str(local))}],
    )["records"][0][0]["stringValue"]

    # 2) clean REPLACE: drop any prior period + cells for (union, start_date).
    rds.execute_statement(
        **common,
        sql=("DELETE FROM rate_cells WHERE period_id IN ("
             "SELECT id FROM rate_periods WHERE union_id = :uid::uuid AND start_date = :sd::date)"),
        parameters=[{"name": "uid", "value": _sv(uid)}, {"name": "sd", "value": _sv(start_date)}],
    )
    rds.execute_statement(
        **common,
        sql="DELETE FROM rate_periods WHERE union_id = :uid::uuid AND start_date = :sd::date",
        parameters=[{"name": "uid", "value": _sv(uid)}, {"name": "sd", "value": _sv(start_date)}],
    )

    # 3) new rate_period.
    period_id = str(uuid.uuid4())
    source_files = {"uploads": event.get("uploads") or [], "output_csv": csv_key, "synthesized": True}
    canonical_json = {
        "rows": len(rows),
        "gap_count": len(result.get("gaps") or []),
        "gaps_detail": [
            [g.get("zone", ""), g.get("package", ""), g.get("column", ""), g.get("reason", "")]
            for g in (result.get("gaps") or [])
        ],
        "method": "synthesized",
    }
    end_param = {"name": "ed", "value": ({"isNull": True} if not end_date else _sv(end_date))}
    rds.execute_statement(
        **common,
        sql=("INSERT INTO rate_periods "
             "(id, union_id, start_date, end_date, status, approval_state, version, "
             " source_files, canonical_json) "
             "VALUES (:id::uuid, :uid::uuid, :sd::date, "
             "        CASE WHEN :has_ed THEN :ed::date ELSE NULL END, "
             "        'extracted', 'pending_review', 1, :sf::jsonb, :cj::jsonb)"),
        parameters=[
            {"name": "id", "value": _sv(period_id)},
            {"name": "uid", "value": _sv(uid)},
            {"name": "sd", "value": _sv(start_date)},
            {"name": "has_ed", "value": {"booleanValue": bool(end_date)}},
            end_param,
            {"name": "sf", "value": _sv(json.dumps(source_files))},
            {"name": "cj", "value": _sv(json.dumps(canonical_json))},
        ],
    )

    # 4) cells — one per (row, fund/wage column), cohort in dimensions.
    param_sets: list[list[dict[str, Any]]] = []
    for row in rows:
        zone = row.get("zone") or ""
        package = row.get("package") or ""
        before = row.get("indentured_before")
        after = row.get("indentured_after")
        dims: dict[str, Any] = {}
        if before:
            dims[_COHORT_BEFORE] = before
        if after:
            dims[_COHORT_AFTER] = after
        dims_json = json.dumps(dims) if dims else "{}"
        for col, val in (row.get("cells") or {}).items():
            if val is None:
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            vtype = "percent" if col in percent else "currency"
            param_sets.append([
                {"name": "id", "value": _sv(str(uuid.uuid4()))},
                {"name": "pid", "value": _sv(period_id)},
                {"name": "zone", "value": _sv(zone)},
                {"name": "pkg", "value": _sv(package)},
                {"name": "dim", "value": _sv(dims_json)},
                {"name": "col", "value": _sv(col)},
                {"name": "val", "value": {"doubleValue": fval}},
                {"name": "vt", "value": _sv(vtype)},
                {"name": "prov", "value": _sv(json.dumps(
                    {"method": "synthesized", "source_csv": csv_key}))},
            ])

    cell_sql = (
        "INSERT INTO rate_cells "
        "(id, period_id, zone, package, dimensions, column_name, value, value_type, "
        " provenance, confidence) "
        "VALUES (:id::uuid, :pid::uuid, :zone, :pkg, :dim::jsonb, :col, :val, :vt, "
        "        :prov::jsonb, 1.0)"
    )
    # batch in chunks of 1000 param sets.
    for i in range(0, len(param_sets), 1000):
        rds.batch_execute_statement(**common, sql=cell_sql, parameterSets=param_sets[i:i + 1000])

    logger.info("synth-publish: wrote %d rows / %d cells for local=%s period=%s (period_id=%s)",
                len(rows), len(param_sets), local, start_date, period_id)
    return {
        "published": True,
        "synthesized": True,
        "local": local,
        "period": start_date,
        "period_id": period_id,
        "row_count": len(rows),
        "cell_count": len(param_sets),
        "output_csv": csv_key,
    }
