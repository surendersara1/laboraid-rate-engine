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

        resp = boto3.client("rds-data").execute_statement(
            resourceArn=os.environ["AURORA_CLUSTER_ARN"],
            secretArn=os.environ["AURORA_SECRET_ARN"],
            database="laboraid",
            sql=(
                "SELECT canonical_json, approval_state FROM rate_periods rp "
                "JOIN unions u ON rp.union_id = u.id "
                "WHERE u.local = :local AND rp.start_date = :period::date"
            ),
            parameters=[
                {"name": "local", "value": {"longValue": int(p["local"])}},
                {"name": "period", "value": {"stringValue": p["period"]}},
            ],
        )
        records = resp.get("records", [])
        if not records:
            return _resp({"error": "not_found"}, 404)
        return _resp({"record": records[0]})
    except Exception:
        logger.exception("ratesheet-get failed")
        raise
