"""Rate sheet reject Lambda (Spec/09 §4 L2). Business rejection; requires a reason.

On a successful rejection the handler persists approval_state/rejected_by/
rejected_at/rejection_reason/rejection_tags to Aurora `rate_periods` via the RDS
Data API and emits ``laboraid.rate-sheet.rejected`` to the engine EventBridge bus
(audit B2).
"""

from __future__ import annotations

import json
import os
from typing import Any

import authz  # shared Lambda layer (/opt/python/authz.py)

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


def _actor(event: dict[str, Any]) -> str:
    """Return a human-recognizable actor string from the JWT claims, preferring
    email > cognito:username > sub. The activity timeline renders this verbatim;
    plain UUIDs are useless to a Business reviewer."""
    claims = (
        event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    )
    return (
        claims.get("email") or claims.get("cognito:username") or claims.get("sub") or "unknown"
    )


VALID_TAGS = {"missing_data", "wrong_extraction", "cba_mismatch", "other"}


def reject_transition(
    approval_state: str, reason: str, tags: list[str] | None = None
) -> tuple[int, dict[str, Any]]:
    """Decide the reject transition. Returns (http_status, body)."""
    if not reason or not reason.strip():
        return 422, {"error": "reason_required"}
    if approval_state == "published":
        return 409, {"error": "already_published"}
    bad = [t for t in (tags or []) if t not in VALID_TAGS]
    if bad:
        return 422, {"error": "invalid_tags", "invalid": bad}
    return 200, {"approval_state": "rejected", "rejection_reason": reason}


def persist_rejection(
    local: str, period: str, rejected_by: str, reason: str, tags: list[str]
) -> None:
    """Persist the rejection (incl. reason + tags) to Aurora + audit_log."""
    import boto3

    rds = boto3.client("rds-data")
    common = {
        "resourceArn": os.environ["AURORA_CLUSTER_ARN"],
        "secretArn": os.environ["AURORA_SECRET_ARN"],
        "database": "laboraid",
    }
    tags_literal = "{" + ",".join(tags) + "}"
    rds.execute_statement(
        **common,
        sql=(
            "UPDATE rate_periods SET approval_state='rejected', rejected_by=:by, "
            "rejected_at=NOW(), rejection_reason=:reason, "
            "rejection_tags=CAST(:tags AS TEXT[]) "
            "WHERE union_id = (SELECT id FROM unions WHERE local = :local::int) "
            "AND start_date = :period::date"
        ),
        parameters=[
            {"name": "by", "value": {"stringValue": rejected_by}},
            {"name": "reason", "value": {"stringValue": reason}},
            {"name": "tags", "value": {"stringValue": tags_literal}},
            {"name": "local", "value": {"stringValue": str(local)}},
            {"name": "period", "value": {"stringValue": period}},
        ],
    )
    rds.execute_statement(
        **common,
        sql=(
            "INSERT INTO audit_log (tenant, actor, action, details) "
            "VALUES ('laboraid', :actor, 'reject', :details::jsonb)"
        ),
        parameters=[
            {"name": "actor", "value": {"stringValue": rejected_by}},
            {
                "name": "details",
                "value": {
                    "stringValue": json.dumps({
                        "local": str(local),
                        "period": period,
                        "reason": reason,
                        "tags": tags,
                    })
                },
            },
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


# Per-route Cognito group gate (Spec/09 §2.2, audit B3).
ALLOWED_GROUPS = ["Business"]


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        denied = authz.enforce_groups(event, ALLOWED_GROUPS)
        if denied:
            return denied
        body = json.loads(event.get("body") or "{}")
        params = event.get("pathParameters") or {}
        local, period = params.get("local"), params.get("period")
        reason = body.get("reason", "")
        tags = body.get("tags") or []
        status, result = reject_transition(
            body.get("approval_state", "pending_review"),
            reason,
            tags,
        )
        if status == 200:
            if not local or not period:
                return _resp({"error": "missing_path_params"}, 400)
            rejected_by = _actor(event)
            persist_rejection(local, period, rejected_by, reason, tags)
            emit_event(
                "laboraid.rate-sheet.rejected",
                {
                    "local": local,
                    "period": period,
                    "rejected_by": rejected_by,
                    "rejection_reason": reason,
                    "rejection_tags": tags,
                },
            )
            result["rejected_by"] = rejected_by
        return _resp(result, status)
    except Exception:
        logger.exception("ratesheet-reject failed")
        raise
