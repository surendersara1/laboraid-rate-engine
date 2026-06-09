"""Rate sheet audit Lambda (Spec/09 §4 L2). Full audit trail for a rate sheet.

Returns a structured feed (newest first) of every approve/reject/comment/
override event for a single (local, period). The Business activity timeline
calls this; the response shape is {records: [{ts, actor, action, details}]}.
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


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        p = event["pathParameters"]
        import boto3

        resp = boto3.client("rds-data").execute_statement(
            resourceArn=os.environ["AURORA_CLUSTER_ARN"],
            secretArn=os.environ["AURORA_SECRET_ARN"],
            database="laboraid",
            sql=(
                "SELECT id, to_char(ts AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), "
                "       actor, action, COALESCE(details::text,'{}') "
                "  FROM audit_log "
                " WHERE details->>'local' = :local AND details->>'period' = :period "
                " ORDER BY ts DESC LIMIT 200"
            ),
            parameters=[
                {"name": "local", "value": {"stringValue": str(p["local"])}},
                {"name": "period", "value": {"stringValue": p["period"]}},
            ],
        )

        records: list[dict[str, Any]] = []
        for row in resp.get("records", []):
            try:
                details = json.loads(row[4].get("stringValue", "{}"))
            except Exception:
                details = {}
            records.append({
                "id": row[0].get("longValue") or row[0].get("stringValue"),
                "ts": row[1].get("stringValue"),
                "actor": row[2].get("stringValue"),
                "action": row[3].get("stringValue"),
                "details": details,
            })
        return _resp({"records": records, "count": len(records)})
    except Exception:
        logger.exception("ratesheet-audit failed")
        raise
