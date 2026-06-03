"""Rate sheet list Lambda (Spec/09 §4 L2). Lists rate periods by approval_state. Cognito/M2M."""

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
        params = event.get("queryStringParameters") or {}
        state = params.get("approval_state")
        sql = "SELECT id, union_id, start_date, approval_state FROM rate_periods"
        if state:
            sql += " WHERE approval_state = :state"
        sql += " ORDER BY start_date DESC LIMIT 200"
        import boto3

        kwargs: dict[str, Any] = dict(
            resourceArn=os.environ["AURORA_CLUSTER_ARN"],
            secretArn=os.environ["AURORA_SECRET_ARN"],
            database="laboraid",
            sql=sql,
        )
        if state:
            kwargs["parameters"] = [{"name": "state", "value": {"stringValue": state}}]
        resp = boto3.client("rds-data").execute_statement(**kwargs)
        return _resp({"records": resp.get("records", [])})
    except Exception:
        logger.exception("ratesheet-list failed")
        raise
