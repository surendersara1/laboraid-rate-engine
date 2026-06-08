"""Rate sheet get Lambda (Spec/09 §4 L2). Returns canonical JSON + approval state. Cognito/M2M."""

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
        "body": json.dumps(body),
    }


def _sub(event: dict[str, Any]) -> str:
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
        .get("sub", "unknown")
    )


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        p = event["pathParameters"]
        import boto3

        data = boto3.client("rds-data")
        # 1. Fetch the rate_period header (approval_state + source_files for PDF link)
        head = data.execute_statement(
            resourceArn=os.environ["AURORA_CLUSTER_ARN"],
            secretArn=os.environ["AURORA_SECRET_ARN"],
            database="laboraid",
            sql=(
                "SELECT rp.id::text, rp.approval_state, "
                "       COALESCE(rp.source_files::text, '{}') AS source_files, "
                "       COALESCE(rp.canonical_json::text, '{}') AS canonical_json "
                "  FROM rate_periods rp "
                "  JOIN unions u ON rp.union_id = u.id "
                " WHERE u.local = :local::int AND rp.start_date = :period::date"
            ),
            parameters=[
                {"name": "local", "value": {"stringValue": str(p["local"])}},
                {"name": "period", "value": {"stringValue": p["period"]}},
            ],
        )
        rows = head.get("records", [])
        if not rows:
            return _resp({"error": "not_found"}, 404)
        period_id = rows[0][0]["stringValue"]
        approval_state = rows[0][1]["stringValue"]
        source_files = json.loads(rows[0][2].get("stringValue", "{}"))

        # 2. Fetch all rate_cells for this period
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

        # Build a presigned URL to the source PDF if we know its S3 key.
        source_pdf_url = None
        pdf_key = source_files.get("rate_notice") or source_files.get("pdf")
        if pdf_key:
            try:
                s3 = boto3.client("s3")
                source_pdf_url = s3.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": os.environ.get("INPUTS_BUCKET", "laboraid-dev-l3-bucket-inputs"),
                        "Key": pdf_key,
                    },
                    ExpiresIn=3600,
                )
            except Exception:  # pragma: no cover
                logger.exception("presign failed")

        return _resp({
            "id": period_id,
            "approval_state": approval_state,
            "cells": cells,
            "source_pdf_url": source_pdf_url,
            "source_files": source_files,
        })
    except Exception:
        logger.exception("ratesheet-get failed")
        raise
