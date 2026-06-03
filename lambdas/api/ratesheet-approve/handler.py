"""Rate sheet approve Lambda (Spec/09 §4 L2). Business sign-off; requires empty review queue.

On a successful transition the handler persists the new state to Aurora
`rate_periods` (approval_state/approved_by/approved_at) via the RDS Data API and
emits ``laboraid.rate-sheet.approved`` to the engine EventBridge bus, so the
approval is durable and observable — not just an HTTP response (audit B2).
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


def approve_transition(
    approval_state: str, review_queue_empty: bool
) -> tuple[int, dict[str, Any]]:
    """Decide the approve transition. Returns (http_status, body)."""
    if not review_queue_empty:
        return 422, {"error": "review_queue_not_empty"}
    if approval_state not in ("pending_review", "rejected"):
        return 409, {"error": "not_approvable", "approval_state": approval_state}
    return 200, {"approval_state": "approved"}


def persist_approval(local: str, period: str, approved_by: str) -> None:
    """Persist the approval to Aurora `rate_periods` via the RDS Data API."""
    import boto3

    sql = (
        "UPDATE rate_periods SET approval_state='approved', approved_by=:by, "
        "approved_at=NOW() "
        "WHERE union_id = (SELECT id FROM unions WHERE local = :local) "
        "AND start_date = :period"
    )
    boto3.client("rds-data").execute_statement(
        resourceArn=os.environ["AURORA_CLUSTER_ARN"],
        secretArn=os.environ["AURORA_SECRET_ARN"],
        database="laboraid",
        sql=sql,
        parameters=[
            {"name": "by", "value": {"stringValue": approved_by}},
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
        status, result = approve_transition(
            body.get("approval_state", "pending_review"),
            bool(body.get("review_queue_empty", False)),
        )
        if status == 200:
            if not local or not period:
                return _resp({"error": "missing_path_params"}, 400)
            approver = _sub(event)
            persist_approval(local, period, approver)
            emit_event(
                "laboraid.rate-sheet.approved",
                {"local": local, "period": period, "approved_by": approver},
            )
            result["approved_by"] = approver
        return _resp(result, status)
    except Exception:
        logger.exception("ratesheet-approve failed")
        raise
