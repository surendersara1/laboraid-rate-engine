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


def approve_transition(
    approval_state: str,
    review_queue_empty: bool,
    actor: str,
    reviewed_by: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Decide the next transition in the dual-control state machine.

    Per Dan's SOP §6 + the integration brief: two distinct humans must touch
    every sheet — a reviewer marks it reviewed (`pending_review` →
    `pending_approval`), then a *different* actor approves it
    (`pending_approval` → `approved`). The single endpoint dispatches based
    on current state so the UI only needs one button per state.
    """
    if not review_queue_empty:
        return 422, {"error": "review_queue_not_empty"}
    if approval_state in ("pending_review", "rejected"):
        return 200, {"approval_state": "pending_approval", "stage": "review"}
    if approval_state == "pending_approval":
        if reviewed_by and actor == reviewed_by:
            return 409, {"error": "dual_control_violation", "reviewed_by": reviewed_by}
        return 200, {"approval_state": "approved", "stage": "approve"}
    return 409, {"error": "not_approvable", "approval_state": approval_state}


def _rds_common() -> dict[str, str]:
    return {
        "resourceArn": os.environ["AURORA_CLUSTER_ARN"],
        "secretArn": os.environ["AURORA_SECRET_ARN"],
        "database": "laboraid",
    }


def fetch_current(local: str, period: str) -> tuple[str, str | None]:
    """Return (approval_state, reviewed_by) for the period; ('missing', None) if absent."""
    import boto3

    res = boto3.client("rds-data").execute_statement(
        **_rds_common(),
        sql=(
            "SELECT approval_state, reviewed_by FROM rate_periods "
            "WHERE union_id = (SELECT id FROM unions WHERE local = :local::int) "
            "AND start_date = :period::date"
        ),
        parameters=[
            {"name": "local", "value": {"stringValue": str(local)}},
            {"name": "period", "value": {"stringValue": period}},
        ],
    )
    rows = res.get("records", [])
    if not rows:
        return "missing", None
    state = rows[0][0].get("stringValue")
    rev = rows[0][1].get("stringValue") if not rows[0][1].get("isNull") else None
    return state or "missing", rev


def persist_review(local: str, period: str, reviewed_by: str) -> None:
    """Stage 1: reviewer marks the sheet reviewed (pending_review → pending_approval)."""
    import boto3

    rds = boto3.client("rds-data")
    rds.execute_statement(
        **_rds_common(),
        sql=(
            "UPDATE rate_periods SET approval_state='pending_approval', "
            "reviewed_by=:by, reviewed_at=NOW() "
            "WHERE union_id = (SELECT id FROM unions WHERE local = :local::int) "
            "AND start_date = :period::date"
        ),
        parameters=[
            {"name": "by", "value": {"stringValue": reviewed_by}},
            {"name": "local", "value": {"stringValue": str(local)}},
            {"name": "period", "value": {"stringValue": period}},
        ],
    )
    rds.execute_statement(
        **_rds_common(),
        sql=(
            "INSERT INTO audit_log (tenant, actor, action, details) "
            "VALUES ('laboraid', :actor, 'review', :details::jsonb)"
        ),
        parameters=[
            {"name": "actor", "value": {"stringValue": reviewed_by}},
            {
                "name": "details",
                "value": {
                    "stringValue": json.dumps({"local": str(local), "period": period})
                },
            },
        ],
    )


def persist_approval(local: str, period: str, approved_by: str) -> None:
    """Stage 2: approver signs off (pending_approval → approved)."""
    import boto3

    rds = boto3.client("rds-data")
    rds.execute_statement(
        **_rds_common(),
        sql=(
            "UPDATE rate_periods SET approval_state='approved', approved_by=:by, "
            "approved_at=NOW() "
            "WHERE union_id = (SELECT id FROM unions WHERE local = :local::int) "
            "AND start_date = :period::date"
        ),
        parameters=[
            {"name": "by", "value": {"stringValue": approved_by}},
            {"name": "local", "value": {"stringValue": str(local)}},
            {"name": "period", "value": {"stringValue": period}},
        ],
    )
    rds.execute_statement(
        **_rds_common(),
        sql=(
            "INSERT INTO audit_log (tenant, actor, action, details) "
            "VALUES ('laboraid', :actor, 'approve', :details::jsonb)"
        ),
        parameters=[
            {"name": "actor", "value": {"stringValue": approved_by}},
            {
                "name": "details",
                "value": {
                    "stringValue": json.dumps({"local": str(local), "period": period})
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
        if not local or not period:
            return _resp({"error": "missing_path_params"}, 400)
        actor = _actor(event)
        # Source-of-truth state + reviewer come from Aurora — the client
        # supplies review_queue_empty (UI-derived) but we don't trust its
        # approval_state. This keeps the dual-control gate enforceable.
        current_state, reviewed_by = fetch_current(local, period)
        if current_state == "missing":
            return _resp({"error": "rate_period_not_found"}, 404)
        status, result = approve_transition(
            current_state,
            bool(body.get("review_queue_empty", False)),
            actor=actor,
            reviewed_by=reviewed_by,
        )
        if status == 200:
            stage = result.pop("stage")
            if stage == "review":
                persist_review(local, period, actor)
                emit_event(
                    "laboraid.rate-sheet.reviewed",
                    {"local": local, "period": period, "reviewed_by": actor},
                )
                result["reviewed_by"] = actor
            else:
                persist_approval(local, period, actor)
                emit_event(
                    "laboraid.rate-sheet.approved",
                    {"local": local, "period": period, "approved_by": actor},
                )
                result["approved_by"] = actor
        return _resp(result, status)
    except Exception:
        logger.exception("ratesheet-approve failed")
        raise
