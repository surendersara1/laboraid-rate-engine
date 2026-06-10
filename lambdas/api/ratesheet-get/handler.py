"""Rate sheet get Lambda (Spec/09 §4 L2). Returns canonical JSON + approval
state, presigned URLs to every artifact, and the most recent SFN job that
produced this rate sheet (job_id + duration + status). Cognito-authenticated.
"""

from __future__ import annotations

import json
import os
from typing import Any

try:  # pragma: no cover - present in the Lambda runtime
    from aws_lambda_powertools import Logger, Tracer

    logger = Logger(service="laboraid-api")
    tracer = Tracer()

    def _instrument(fn: Any) -> Any:
        return logger.inject_lambda_context(tracer.capture_lambda_handler(fn))

except ModuleNotFoundError:  # pragma: no cover - offline unit-test env
    import logging

    logger = logging.getLogger("laboraid-api")  # type: ignore[assignment]

    def _instrument(fn: Any) -> Any:
        return fn


def _resp(body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


STATE_MACHINE_ARN = os.environ.get(
    "STATE_MACHINE_ARN",
    "arn:aws:states:us-east-2:908106425069:stateMachine:laboraid-dev-l3-sfn-main",
)
INPUTS_BUCKET = os.environ.get("INPUTS_BUCKET", "laboraid-dev-l3-bucket-inputs")
OUTPUTS_BUCKET = os.environ.get("OUTPUTS_BUCKET", "laboraid-dev-l3-bucket-outputs")


def _latest_job_for_source(sfn: Any, source_key: str) -> dict[str, Any] | None:
    """Find the most recent SFN execution whose EventBridge input refers to
    this S3 source key. Used so the Business header can link to the Admin
    job page that produced this rate sheet.
    """
    if not source_key:
        return None
    try:
        execs = sfn.list_executions(
            stateMachineArn=STATE_MACHINE_ARN, maxResults=50
        )["executions"]
    except Exception:
        return None
    for e in execs:
        try:
            desc = sfn.describe_execution(executionArn=e["executionArn"])
            inp = json.loads(desc.get("input", "{}"))
            if inp.get("detail", {}).get("object", {}).get("key") == source_key:
                start, stop = e.get("startDate"), e.get("stopDate")
                duration_ms = (
                    int((stop - start).total_seconds() * 1000)
                    if start and stop
                    else None
                )
                return {
                    "job_id": e["name"],
                    "status": e["status"],
                    "started_at": start.isoformat() if start else None,
                    "stopped_at": stop.isoformat() if stop else None,
                    "duration_ms": duration_ms,
                }
        except Exception:
            continue
    return None


def _presign(s3: Any, bucket: str, key: str) -> str | None:
    if not key:
        return None
    try:
        return s3.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=3600
        )
    except Exception:
        return None


def _head_size(s3: Any, bucket: str, key: str) -> int | None:
    try:
        return s3.head_object(Bucket=bucket, Key=key).get("ContentLength")
    except Exception:
        return None


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        p = event["pathParameters"]
        qs = event.get("queryStringParameters") or {}
        requested_version = qs.get("version")  # optional ?version=2
        import boto3

        data = boto3.client("rds-data")
        s3 = boto3.client("s3")
        sfn = boto3.client("stepfunctions")

        # Pull every version for this {local, period} so the UI can render a
        # version pill / switcher. Newest version is the default selection.
        versions_resp = data.execute_statement(
            resourceArn=os.environ["AURORA_CLUSTER_ARN"],
            secretArn=os.environ["AURORA_SECRET_ARN"],
            database="laboraid",
            sql=(
                "SELECT rp.id::text, rp.version, rp.parent_version, "
                "       rp.approval_state, "
                "       COALESCE(rp.rework_context::text,'null') "
                "  FROM rate_periods rp "
                "  JOIN unions u ON u.id = rp.union_id "
                " WHERE u.local = :local::int AND rp.start_date = :period::date "
                " ORDER BY rp.version DESC"
            ),
            parameters=[
                {"name": "local", "value": {"stringValue": str(p["local"])}},
                {"name": "period", "value": {"stringValue": p["period"]}},
            ],
        )
        all_versions = []
        for vrow in versions_resp.get("records", []):
            all_versions.append({
                "period_id": vrow[0].get("stringValue"),
                "version": vrow[1].get("longValue") or 1,
                "parent_version": vrow[2].get("longValue"),
                "approval_state": vrow[3].get("stringValue"),
                "rework_context": json.loads(vrow[4].get("stringValue", "null") or "null"),
            })
        if not all_versions:
            return _resp({"error": "not_found"}, 404)

        # Pick the version to render: caller-specified, else newest.
        selected = all_versions[0]
        if requested_version is not None:
            try:
                want = int(requested_version)
                match = next((v for v in all_versions if v["version"] == want), None)
                if match is not None:
                    selected = match
            except ValueError:
                pass

        head = data.execute_statement(
            resourceArn=os.environ["AURORA_CLUSTER_ARN"],
            secretArn=os.environ["AURORA_SECRET_ARN"],
            database="laboraid",
            sql=(
                "SELECT rp.id::text, rp.approval_state, "
                "       COALESCE(rp.source_files::text, '{}') AS source_files, "
                "       COALESCE(rp.canonical_json::text, '{}') AS canonical_json, "
                "       u.local, u.trade, rp.version, rp.parent_version "
                "  FROM rate_periods rp "
                "  JOIN unions u ON rp.union_id = u.id "
                " WHERE rp.id = :pid::uuid"
            ),
            parameters=[
                {"name": "pid", "value": {"stringValue": selected["period_id"]}},
            ],
        )
        rows = head.get("records", [])
        if not rows:
            return _resp({"error": "not_found"}, 404)
        period_id = rows[0][0]["stringValue"]
        approval_state = rows[0][1]["stringValue"]
        source_files = json.loads(rows[0][2].get("stringValue", "{}"))
        canonical_summary = json.loads(rows[0][3].get("stringValue", "{}"))
        local = rows[0][4].get("longValue")
        trade = rows[0][5].get("stringValue") or ""
        version = rows[0][6].get("longValue") or 1
        parent_version = rows[0][7].get("longValue")

        # Pull every cell. Order by package then column_name for deterministic UI.
        cells_resp = data.execute_statement(
            resourceArn=os.environ["AURORA_CLUSTER_ARN"],
            secretArn=os.environ["AURORA_SECRET_ARN"],
            database="laboraid",
            sql=(
                "SELECT id::text, zone, package, column_name, value::text, "
                "       COALESCE(confidence, 1.0)::text, "
                "       COALESCE(provenance::text, '{}') "
                "  FROM rate_cells "
                " WHERE period_id = :pid::uuid "
                " ORDER BY package, column_name"
            ),
            parameters=[{"name": "pid", "value": {"stringValue": period_id}}],
        )
        cells: list[dict[str, Any]] = []
        for row in cells_resp.get("records", []):
            cells.append({
                "cell_id": row[0].get("stringValue"),
                "zone": row[1].get("stringValue"),
                "package": row[2].get("stringValue"),
                "column_name": row[3].get("stringValue"),
                "value": float(row[4].get("stringValue", "0")),
                "confidence": float(row[5].get("stringValue", "1.0")),
                "provenance": json.loads(row[6].get("stringValue", "{}")),
            })

        # Build the full artifact list. Source PDF is the input; output_csv,
        # output_xlsx, gap_report_json may be present in source_files JSON. We
        # presign + size each one that's actually in S3 so the UI can render
        # "open" links with confidence; missing artifacts show as "not produced".
        artifact_specs: list[tuple[str, str, str, str]] = [
            ("Source PDF", "input", INPUTS_BUCKET,
             source_files.get("rate_notice") or source_files.get("pdf") or ""),
            ("Canonical CSV", "output", OUTPUTS_BUCKET,
             source_files.get("output_csv") or ""),
            ("Excel (xlsx)", "output", OUTPUTS_BUCKET,
             source_files.get("output_xlsx") or ""),
            ("Gap report (JSON)", "output", OUTPUTS_BUCKET,
             source_files.get("gap_report") or ""),
        ]
        artifacts: list[dict[str, Any]] = []
        for name, kind, bucket, key in artifact_specs:
            if not key:
                # Still emit the row so the UI can show "not produced" for the
                # ones not yet in this run.
                artifacts.append({
                    "name": name, "kind": kind, "bucket": bucket, "key": "",
                    "size": None, "url": None,
                })
                continue
            size = _head_size(s3, bucket, key)
            artifacts.append({
                "name": name,
                "kind": kind,
                "bucket": bucket,
                "key": key,
                "size": size,
                "url": _presign(s3, bucket, key) if size is not None else None,
            })

        # Resolve the source PDF URL up-top too for the inline viewer.
        source_pdf_url = next(
            (a["url"] for a in artifacts if a["name"] == "Source PDF" and a.get("url")),
            None,
        )

        # Walk the SFN to find the run that produced this rate sheet.
        source_key = source_files.get("rate_notice") or source_files.get("pdf") or ""
        job_meta = _latest_job_for_source(sfn, source_key)

        # Pull counts from canonical_json if present (extractor writes them).
        # gap_count = total NULL cells; gaps_detail = kernel-emitted
        # [zone, package, column, reason] tuples explaining why each was
        # left blank. The UI uses gaps_detail to render the "needs more
        # input" banner with actionable reasons.
        gap_count = (
            canonical_summary.get("gap_count")
            if canonical_summary.get("gap_count") is not None
            else canonical_summary.get("gaps")
        )
        counts = {
            "classifications": canonical_summary.get("rows"),
            "cells": len(cells),
            "gaps": gap_count or 0,
        }
        gaps_detail = canonical_summary.get("gaps_detail") or []

        return _resp({
            "id": period_id,
            "union": f"{trade} {local}".strip() if local else trade,
            "trade": trade,
            "local": local,
            "period": p["period"],
            "approval_state": approval_state,
            "cells": cells,
            "source_pdf_url": source_pdf_url,
            "source_files": source_files,
            "artifacts": artifacts,
            "job_meta": job_meta,
            "counts": counts,
            "gaps_detail": gaps_detail,
            "canonical_summary": canonical_summary,
            # Tier 3: versioning. `version` is which one we just returned, and
            # `versions` is the full list (newest first) so the UI can offer a
            # switcher. `parent_version` is non-null only for rework children.
            "version": version,
            "parent_version": parent_version,
            "versions": all_versions,
        })
    except Exception:
        logger.exception("ratesheet-get failed")
        raise
