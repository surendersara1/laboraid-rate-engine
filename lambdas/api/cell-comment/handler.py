"""Cell comment Lambda (Spec/09 §4 L2). Writes a comment to audit_log (action=comment). Business."""

from __future__ import annotations

import json
import os
from typing import Any

import authz  # shared Lambda layer (/opt/python/authz.py)

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


# Per-route Cognito group gate (Spec/09 §2.2, audit B3).
ALLOWED_GROUPS = ["Business"]


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        denied = authz.enforce_groups(event, ALLOWED_GROUPS)
        if denied:
            return denied
        cell_id = event["pathParameters"]["cell_id"]
        body = json.loads(event.get("body") or "{}")
        import boto3

        rds = boto3.client("rds-data")
        # Look up the period the cell belongs to so the activity feed can
        # scope per-rate-sheet without an extra query later.
        scope = rds.execute_statement(
            resourceArn=os.environ["AURORA_CLUSTER_ARN"],
            secretArn=os.environ["AURORA_SECRET_ARN"],
            database="laboraid",
            sql=(
                "SELECT u.local::text, to_char(rp.start_date,'YYYY-MM-DD') "
                "  FROM rate_cells rc "
                "  JOIN rate_periods rp ON rp.id = rc.period_id "
                "  JOIN unions u ON u.id = rp.union_id "
                " WHERE rc.id = :id::uuid"
            ),
            parameters=[{"name": "id", "value": {"stringValue": cell_id}}],
        )
        local = period = None
        if scope.get("records"):
            local = scope["records"][0][0].get("stringValue")
            period = scope["records"][0][1].get("stringValue")

        details = {
            "cell_id": cell_id,
            "text": body.get("text", ""),
            "local": local,
            "period": period,
        }
        rds.execute_statement(
            resourceArn=os.environ["AURORA_CLUSTER_ARN"],
            secretArn=os.environ["AURORA_SECRET_ARN"],
            database="laboraid",
            sql=(
                "INSERT INTO audit_log (tenant, actor, action, details) "
                "VALUES ('laboraid', :actor, 'comment', :details::jsonb)"
            ),
            parameters=[
                {"name": "actor", "value": {"stringValue": _sub(event)}},
                {"name": "details", "value": {"stringValue": json.dumps(details)}},
            ],
        )
        return _resp({"cell_id": cell_id, "status": "commented", "local": local, "period": period})
    except Exception:
        logger.exception("cell-comment failed")
        raise
