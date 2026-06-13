"""Cell comment Lambda (Spec/09 §4 L2). Writes a comment to audit_log (action=comment). Business."""

from __future__ import annotations

import json
import os
import uuid
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
        common = {
            "resourceArn": os.environ["AURORA_CLUSTER_ARN"],
            "secretArn": os.environ["AURORA_SECRET_ARN"],
            "database": "laboraid",
        }
        # Look up the cell's coordinates + period so we can record a structured
        # correction row (and scope the activity feed).
        scope = rds.execute_statement(
            **common,
            sql=(
                "SELECT u.local::text, to_char(rp.start_date,'YYYY-MM-DD'), "
                "       rp.id::text, rp.version, rc.zone, rc.package, rc.column_name "
                "  FROM rate_cells rc "
                "  JOIN rate_periods rp ON rp.id = rc.period_id "
                "  JOIN unions u ON u.id = rp.union_id "
                " WHERE rc.id = :id::uuid"
            ),
            parameters=[{"name": "id", "value": {"stringValue": cell_id}}],
        )
        local = period = period_id = zone = package = column_name = None
        version = 1
        if scope.get("records"):
            r = scope["records"][0]
            local = r[0].get("stringValue")
            period = r[1].get("stringValue")
            period_id = r[2].get("stringValue")
            version = r[3].get("longValue", 1)
            zone = r[4].get("stringValue") if not r[4].get("isNull") else None
            package = r[5].get("stringValue")
            column_name = r[6].get("stringValue")

        text = body.get("text", "")
        actor = _actor(event)

        # Structured correction row (legal record) — only when the cell resolves.
        if period_id:
            rds.execute_statement(
                **common,
                sql=(
                    "INSERT INTO cell_corrections (id, period_id, version, cell_id, "
                    "  union_local, period, zone, package, column_name, kind, "
                    "  reason, actor, status) "
                    "VALUES (:id::uuid, :pid::uuid, :ver, :cid::uuid, :local, :period, "
                    "  :zone, :package, :col, 'comment', :reason, :actor, 'open')"
                ),
                parameters=[
                    {"name": "id", "value": {"stringValue": str(uuid.uuid4())}},
                    {"name": "pid", "value": {"stringValue": period_id}},
                    {"name": "ver", "value": {"longValue": int(version)}},
                    {"name": "cid", "value": {"stringValue": cell_id}},
                    {"name": "local", "value": {"stringValue": local or ""}},
                    {"name": "period", "value": {"stringValue": period or ""}},
                    {"name": "zone", "value": ({"stringValue": zone} if zone else {"isNull": True})},
                    {"name": "package", "value": {"stringValue": package or ""}},
                    {"name": "col", "value": {"stringValue": column_name or ""}},
                    {"name": "reason", "value": {"stringValue": text}},
                    {"name": "actor", "value": {"stringValue": actor}},
                ],
            )

        # Activity-feed mirror.
        details = {"cell_id": cell_id, "text": text, "local": local, "period": period}
        rds.execute_statement(
            **common,
            sql=(
                "INSERT INTO audit_log (tenant, actor, action, details) "
                "VALUES ('laboraid', :actor, 'comment', :details::jsonb)"
            ),
            parameters=[
                {"name": "actor", "value": {"stringValue": actor}},
                {"name": "details", "value": {"stringValue": json.dumps(details)}},
            ],
        )
        return _resp({"cell_id": cell_id, "status": "commented", "local": local, "period": period})
    except Exception:
        logger.exception("cell-comment failed")
        raise
