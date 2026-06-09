"""Publisher Lambda — write the agent's extraction into Aurora.

The Step Functions pipeline's old "Publish" state was just `sfn.Succeed()` — it
terminated the workflow without ever writing the canonical rate sheet to Aurora.
This Lambda fills that gap. It reads the agent's CSV from S3, parses it into
classification rows + cells, and UPSERTs:

  1. `unions`        — (local, trade) pair from the CSV's metadata columns.
  2. `rate_periods`  — one row per {union, period}, version=1.
  3. `rate_cells`    — N cells per period (zone, package, column_name, value)
                       with provenance carrying the agent's method + source.

After this runs, the same PDF can be uploaded twice without producing two rows
(idempotent by (union, period)). Existing rate_periods are SKIPPED — the rework
loop is the documented path to revise a published period.

Input shape (SFN state at "Publish" time — orchestration_stack uses
result_path="$.extract" for the ExtractViaAgent task):
  {
    "classify": {
      "s3_key": "<source PDF key>",
      "union": "sprinkler_fitters_704" | "local_<N>",
      "local": "704",
      "period": "YYYY-MM-DD",
      "doc_type": "rate_notice",
      ...
    },
    "extract": {
      "canonical": {
        "s3_key": "<agent's CSV S3 key>",
        "rows": 13,
        "gaps": [["zone","package","column","reason"], ...],
        "gap_count": 1,
        ...
      },
      "runtime_response": { ... }
    },
    ...
  }
"""

from __future__ import annotations

import csv
import io
import json
import os
import uuid
from typing import Any

try:  # pragma: no cover - present in the Lambda runtime
    from aws_lambda_powertools import Logger, Tracer

    logger = Logger(service="laboraid-publisher")
    tracer = Tracer()

    def _instrument(fn: Any) -> Any:
        return logger.inject_lambda_context(tracer.capture_lambda_handler(fn))

except ModuleNotFoundError:  # pragma: no cover - offline unit-test env
    import logging

    logger = logging.getLogger("laboraid-publisher")  # type: ignore[assignment]

    def _instrument(fn: Any) -> Any:
        return fn


OUTPUTS_BUCKET = os.environ.get("OUTPUTS_BUCKET", "laboraid-dev-l3-bucket-outputs")

# Columns the kernel/agent emits as leading metadata before the rate-value
# columns. Detected by name (not position) because the order is fixed but new
# profiles could prepend more.
METADATA_COLUMNS = {
    "Union Group",
    "Trade",
    "Union Local",
    "Zone",
    "Package",
    "Start Date",
    "End Date",
}


def _parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _metadata_column_indices(header: list[str]) -> dict[str, int]:
    return {name: idx for idx, name in enumerate(header) if name in METADATA_COLUMNS}


def _rate_column_indices(header: list[str]) -> list[tuple[int, str]]:
    return [(idx, name) for idx, name in enumerate(header) if name not in METADATA_COLUMNS]


def _coerce_float(s: str) -> float | None:
    """Convert a CSV cell to a numeric. Returns None for blanks / unparseable.
    The schema column is NUMERIC so we either insert a number or null."""
    if s is None:
        return None
    s = s.strip()
    if not s or s.lower() in ("null", "none", "n/a"):
        return None
    # Strip $ and commas if any user-driven path emitted them.
    s = s.replace("$", "").replace(",", "")
    # Percentages — strip and divide by 100 to keep the column NUMERIC.
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _upsert_union(
    rds: Any,
    common: dict[str, Any],
    local: str,
    trade: str,
    parent_intl: str,
) -> str:
    """Return the UUID of the unions row for (local). Inserts if missing."""
    r = rds.execute_statement(
        **common,
        sql="SELECT id::text FROM unions WHERE local = :local::int",
        parameters=[{"name": "local", "value": {"stringValue": str(local)}}],
    )
    records = r.get("records") or []
    if records:
        return records[0][0]["stringValue"]
    new_id = str(uuid.uuid4())
    rds.execute_statement(
        **common,
        sql=(
            "INSERT INTO unions (id, local, trade, parent_intl) "
            "VALUES (:id::uuid, :local::int, :trade, :parent)"
        ),
        parameters=[
            {"name": "id", "value": {"stringValue": new_id}},
            {"name": "local", "value": {"stringValue": str(local)}},
            {"name": "trade", "value": {"stringValue": trade}},
            {"name": "parent", "value": {"stringValue": parent_intl}},
        ],
    )
    logger.info("publisher: created unions row local=%s trade=%s", local, trade)
    return new_id


def _publish(
    *,
    rds: Any,
    s3: Any,
    common: dict[str, Any],
    classify: dict[str, Any],
    canonical: dict[str, Any],
) -> dict[str, Any]:
    """Read CSV, classify metadata, write Aurora rows. Returns a summary."""
    csv_key = canonical.get("s3_key") or ""
    if not csv_key:
        raise RuntimeError("publisher: no canonical CSV key in agent response")
    body = s3.get_object(Bucket=OUTPUTS_BUCKET, Key=csv_key)["Body"].read()
    header, data_rows = _parse_csv(body.decode("utf-8"))
    if not header or not data_rows:
        raise RuntimeError(f"publisher: empty CSV at s3://{OUTPUTS_BUCKET}/{csv_key}")

    meta_idx = _metadata_column_indices(header)
    rate_cols = _rate_column_indices(header)
    if "Package" not in meta_idx:
        raise RuntimeError("publisher: CSV missing required 'Package' column")

    # Derive union/trade/local/period from the CSV's first data row (the kernel
    # writes the same metadata on every row), falling back to classify.
    first = data_rows[0]
    def col(name: str, default: str = "") -> str:
        idx = meta_idx.get(name)
        return first[idx].strip() if idx is not None and idx < len(first) else default

    local = (col("Union Local") or str(classify.get("local") or "")).strip()
    trade = col("Trade") or (classify.get("union") or "").split("_")[-1].title()
    parent_intl = col("Union Group") or "UA"
    start_date = _normalize_date(col("Start Date")) or classify.get("period")
    end_date = _normalize_date(col("End Date"))

    if not local or not start_date:
        raise RuntimeError(
            f"publisher: cannot determine union/local + period — local={local!r}, "
            f"start_date={start_date!r}"
        )

    # 1) Union UPSERT.
    union_id = _upsert_union(rds, common, local, trade, parent_intl)

    # 2) Idempotency on (union_id, start_date) — skip if a row already exists.
    existing = rds.execute_statement(
        **common,
        sql=(
            "SELECT id::text, version FROM rate_periods "
            " WHERE union_id = :uid::uuid AND start_date = :sd::date "
            " ORDER BY version DESC LIMIT 1"
        ),
        parameters=[
            {"name": "uid", "value": {"stringValue": union_id}},
            {"name": "sd", "value": {"stringValue": start_date}},
        ],
    )
    if existing.get("records"):
        existing_id = existing["records"][0][0]["stringValue"]
        existing_v = existing["records"][0][1].get("longValue") or 1
        logger.info(
            "publisher: rate_period already exists for union=%s period=%s "
            "(v%d, id=%s) — skipping insert; use the rework loop to revise",
            local, start_date, existing_v, existing_id,
        )
        return {
            "published": False,
            "reason": "already_exists",
            "rate_period_id": existing_id,
            "version": existing_v,
            "union_id": union_id,
            "local": local,
            "period": start_date,
        }

    # 3) Insert the rate_period.
    period_id = str(uuid.uuid4())
    source_files = {
        "rate_notice": classify.get("s3_key") or "",
        "output_csv": csv_key,
    }
    canonical_json = {
        "rows": canonical.get("rows"),
        "extracted_rows": canonical.get("extracted_rows"),
        "gaps": canonical.get("gap_count"),
        "checksum": canonical.get("checksum"),
        "extracted_at": canonical.get("extracted_at"),
        "doc_type": classify.get("doc_type"),
    }
    rds.execute_statement(
        **common,
        sql=(
            "INSERT INTO rate_periods "
            "  (id, union_id, start_date, end_date, status, approval_state, "
            "   canonical_json, source_files, version) "
            "VALUES (:id::uuid, :uid::uuid, :sd::date, :ed::date, 'extracted', "
            "        'pending_review', :cj::jsonb, :sf::jsonb, 1)"
        ),
        parameters=[
            {"name": "id", "value": {"stringValue": period_id}},
            {"name": "uid", "value": {"stringValue": union_id}},
            {"name": "sd", "value": {"stringValue": start_date}},
            {
                "name": "ed",
                "value": (
                    {"stringValue": end_date}
                    if end_date
                    else {"isNull": True}
                ),
            },
            {"name": "cj", "value": {"stringValue": json.dumps(canonical_json)}},
            {"name": "sf", "value": {"stringValue": json.dumps(source_files)}},
        ],
    )

    # 4) Insert all rate_cells. Skip blank/null rate values — they go in as
    #    NULL so the UI can show them as gaps. Provenance carries the agent's
    #    method so the UI's "Method" line in the Provenance panel reads true.
    # Method label drives confidence + the Provenance panel's "Method" line.
    # The 5 hand-coded kernel unions are explicit; anything else came from the
    # LLM path. Don't use "_" presence — every normalized union name has one.
    _KERNEL_UNIONS = {
        "pipe_fitters_537",
        "sprinkler_fitters_483",
        "sprinkler_fitters_704",
        "sprinkler_fitters_281",
        "sprinkler_fitters_821",
    }
    method = (
        "kernel"
        if (classify.get("union") or "").lower() in _KERNEL_UNIONS
        else "llm_claude"
    )
    inserted = 0
    skipped = 0
    for row in data_rows:
        zone = row[meta_idx["Zone"]] if "Zone" in meta_idx and meta_idx["Zone"] < len(row) else ""
        package = row[meta_idx["Package"]] if meta_idx["Package"] < len(row) else ""
        if not package:
            skipped += 1
            continue
        for col_idx, col_name in rate_cols:
            if col_idx >= len(row):
                continue
            raw = row[col_idx]
            value = _coerce_float(raw)
            cell_id = str(uuid.uuid4())
            confidence = 1.0 if method == "kernel" else 0.85
            prov = {
                "source": csv_key,
                "method": method,
                "row_raw": str(raw)[:80],
            }
            rds.execute_statement(
                **common,
                sql=(
                    "INSERT INTO rate_cells "
                    "  (id, period_id, zone, package, column_name, value, "
                    "   value_type, provenance, confidence) "
                    "VALUES (:id::uuid, :pid::uuid, :zone, :pkg, :col, "
                    "        :val::numeric, :vt, :prov::jsonb, :conf::numeric)"
                ),
                parameters=[
                    {"name": "id", "value": {"stringValue": cell_id}},
                    {"name": "pid", "value": {"stringValue": period_id}},
                    {"name": "zone", "value": {"stringValue": zone}},
                    {"name": "pkg", "value": {"stringValue": package}},
                    {"name": "col", "value": {"stringValue": col_name}},
                    {
                        "name": "val",
                        "value": (
                            {"stringValue": str(value)}
                            if value is not None
                            else {"isNull": True}
                        ),
                    },
                    {"name": "vt", "value": {"stringValue": "currency"}},
                    {"name": "prov", "value": {"stringValue": json.dumps(prov)}},
                    {
                        "name": "conf",
                        "value": {"stringValue": str(confidence)},
                    },
                ],
            )
            inserted += 1

    return {
        "published": True,
        "rate_period_id": period_id,
        "union_id": union_id,
        "local": local,
        "period": start_date,
        "method": method,
        "cells_inserted": inserted,
        "rows_skipped_no_package": skipped,
    }


def _normalize_date(s: str) -> str | None:
    """Coerce '1/1/26', '2026-01-01', '01-Jan-2026' → 'YYYY-MM-DD'.

    The kernel emits dates as M/D/YY without leading zeros; Aurora's DATE type
    only accepts ISO. Return None when unparseable.
    """
    if not s:
        return None
    s = s.strip()
    # Already ISO?
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    # M/D/YY or M/D/YYYY
    parts = s.replace("-", "/").split("/")
    if len(parts) == 3:
        try:
            m, d, y = (int(p) for p in parts)
            if y < 100:
                y += 2000
            return f"{y:04d}-{m:02d}-{d:02d}"
        except ValueError:
            return None
    return None


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        classify = event.get("classify") or {}
        # The orchestration stack sets result_path="$.extract" for ExtractViaAgent.
        # Older code paths and direct invocations may use $.extractviaagent or a
        # flat $.canonical — tolerate all three.
        agent_state = (
            event.get("extract")
            or event.get("extractviaagent")
            or {}
        )
        canonical = (
            agent_state.get("canonical")
            or event.get("canonical")
            or {}
        )
        if not classify or not canonical:
            raise RuntimeError(
                "publisher: SFN state missing classify or extractviaagent.canonical"
            )

        import boto3

        rds = boto3.client("rds-data")
        s3 = boto3.client("s3")
        common = {
            "resourceArn": os.environ["AURORA_CLUSTER_ARN"],
            "secretArn": os.environ["AURORA_SECRET_ARN"],
            "database": "laboraid",
        }

        result = _publish(
            rds=rds,
            s3=s3,
            common=common,
            classify=classify,
            canonical=canonical,
        )
        logger.info("publisher: %s", json.dumps(result))
        # Emit a lifecycle event so anything observing the bus learns about it.
        bus_name = os.environ.get("ENGINE_BUS_NAME") or ""
        if bus_name and result.get("published"):
            try:
                boto3.client("events").put_events(
                    Entries=[
                        {
                            "Source": "laboraid.pipeline",
                            "DetailType": "laboraid.rate-sheet.created",
                            "Detail": json.dumps(result),
                            "EventBusName": bus_name,
                        }
                    ]
                )
            except Exception as e:  # pragma: no cover
                logger.warning("publisher: event emit failed: %s", e)
        return result
    except Exception:
        logger.exception("publisher failed")
        raise
