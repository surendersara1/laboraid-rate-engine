"""Rate sheet publish Lambda (Spec/09 §4 L2). GATED: 409 unless approval_state='approved'.

The 409 gate reads the *authoritative* approval_state from Aurora `rate_periods`
for the `{local}/{period}` path params — it never trusts the request body (audit
B1). A client cannot POST `{"approval_state":"approved"}` to bypass the gate.
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


def publish_guard(approval_state: str) -> tuple[int, dict[str, Any]]:
    """Publish guard (SOW contract). Returns (http_status, body).

    The publish endpoint MUST return HTTP 409 unless the rate period has been
    approved by the Business persona (Spec/09 §4 L2 §2.2, §4.4).
    """
    if approval_state != "approved":
        return 409, {"error": "not_approved", "approval_state": approval_state}
    return 200, {"approval_state": "published"}


def read_approval_state(local: str, period: str) -> str | None:
    """Read the authoritative approval_state from Aurora for a `{local}/{period}`.

    Joins `unions.local` (the path param) to `rate_periods.union_id` (a UUID FK).
    Returns the state string, or ``None`` when no such rate period exists.
    """
    import boto3

    sql = (
        "SELECT rp.approval_state FROM rate_periods rp "
        "JOIN unions u ON rp.union_id = u.id "
        "WHERE u.local = :local AND rp.start_date = :period "
        "ORDER BY rp.start_date DESC LIMIT 1"
    )
    resp = boto3.client("rds-data").execute_statement(
        resourceArn=os.environ["AURORA_CLUSTER_ARN"],
        secretArn=os.environ["AURORA_SECRET_ARN"],
        database="laboraid",
        sql=sql,
        parameters=[
            {"name": "local", "value": {"longValue": int(local)}},
            {"name": "period", "value": {"stringValue": period}, "typeHint": "DATE"},
        ],
    )
    records = resp.get("records", [])
    if not records:
        return None
    return records[0][0].get("stringValue")


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        params = event.get("pathParameters") or {}
        local, period = params.get("local"), params.get("period")
        if not local or not period:
            return _resp({"error": "missing_path_params"}, 400)
        # Authoritative state from Aurora — the request body is intentionally ignored.
        state = read_approval_state(local, period)
        if state is None:
            return _resp({"error": "not_found"}, 404)
        status, result = publish_guard(state)
        if status == 200:
            result["published_by"] = _sub(event)
        return _resp(result, status)
    except Exception:
        logger.exception("ratesheet-publish failed")
        raise
