"""Rate sheet unapprove Lambda (Spec/09 §4 L2). Original approver only, before publish.

On a successful unapproval the handler resets approval_state to pending_review
(clearing approved_by/approved_at) in Aurora `rate_periods` via the RDS Data API
and emits ``laboraid.rate-sheet.unapproved`` to the engine EventBridge bus — the
unapproved analog of the approved/rejected events (audit B2).
"""

from __future__ import annotations

import json
import os
from typing import Any

ENGINE_BUS_NAME = os.environ.get("ENGINE_BUS_NAME", "")

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


def unapprove_transition(
    approval_state: str, requester_sub: str, approver_sub: str
) -> tuple[int, dict[str, Any]]:
    """Decide the unapprove transition. Returns (http_status, body)."""
    if approval_state == "published":
        return 409, {"error": "already_published"}
    if approval_state != "approved":
        return 409, {"error": "not_approved", "approval_state": approval_state}
    if requester_sub != approver_sub:
        return 403, {"error": "not_original_approver"}
    return 200, {"approval_state": "pending_review"}


def persist_unapproval(local: str, period: str) -> None:
    """Reset the period to pending_review in Aurora via the RDS Data API."""
    import boto3

    sql = (
        "UPDATE rate_periods SET approval_state='pending_review', approved_by=NULL, "
        "approved_at=NULL "
        "WHERE union_id = (SELECT id FROM unions WHERE local = :local) "
        "AND start_date = :period"
    )
    boto3.client("rds-data").execute_statement(
        resourceArn=os.environ["AURORA_CLUSTER_ARN"],
        secretArn=os.environ["AURORA_SECRET_ARN"],
        database="laboraid",
        sql=sql,
        parameters=[
            {"name": "local", "value": {"longValue": int(local)}},
            {"name": "period", "value": {"stringValue": period}, "typeHint": "DATE"},
        ],
    )


def emit_event(detail_type: str, detail: dict[str, Any]) -> None:
    """Emit a rate-sheet lifecycle event to the engine EventBridge bus."""
    import boto3

    boto3.client("events").put_events(
        Entries=[
            {
                "Source": "laboraid.api",
                "DetailType": detail_type,
                "Detail": json.dumps(detail),
                "EventBusName": ENGINE_BUS_NAME,
            }
        ]
    )


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}")
        params = event.get("pathParameters") or {}
        local, period = params.get("local"), params.get("period")
        requester = _sub(event)
        status, result = unapprove_transition(
            body.get("approval_state", "approved"),
            requester,
            body.get("approved_by", ""),
        )
        if status == 200:
            if not local or not period:
                return _resp({"error": "missing_path_params"}, 400)
            persist_unapproval(local, period)
            emit_event(
                "laboraid.rate-sheet.unapproved",
                {"local": local, "period": period, "unapproved_by": requester},
            )
        return _resp(result, status)
    except Exception:
        logger.exception("ratesheet-unapprove failed")
        raise
