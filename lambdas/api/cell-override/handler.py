"""Cell override Lambda (Spec/09 §4 L2). Writes a manual override to DDB and
mirrors it into audit_log so the activity feed shows it. Business persona.
"""

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


ALLOWED_GROUPS = ["Business"]


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        denied = authz.enforce_groups(event, ALLOWED_GROUPS)
        if denied:
            return denied
        cell_id = event["pathParameters"]["cell_id"]
        body = json.loads(event.get("body") or "{}")
        new_value = body.get("value")
        if new_value is None:
            return _resp({"error": "value_required"}, 422)
        try:
            new_value_f = float(new_value)
        except Exception:
            return _resp({"error": "value_must_be_numeric"}, 422)
        justification = body.get("justification") or ""

        import boto3

        rds = boto3.client("rds-data")
        common = {
            "resourceArn": os.environ["AURORA_CLUSTER_ARN"],
            "secretArn": os.environ["AURORA_SECRET_ARN"],
            "database": "laboraid",
        }

        # Look up the existing value + which (union, period) this cell belongs to
        # so we can audit-log the before/after AND scope the DDB row.
        scope = rds.execute_statement(
            **common,
            sql=(
                "SELECT rc.value::text, u.local::text, "
                "       to_char(rp.start_date,'YYYY-MM-DD'), rc.column_name, rc.package, "
                "       rp.id::text, rp.version, rc.zone "
                "  FROM rate_cells rc "
                "  JOIN rate_periods rp ON rp.id = rc.period_id "
                "  JOIN unions u ON u.id = rp.union_id "
                " WHERE rc.id = :id::uuid"
            ),
            parameters=[{"name": "id", "value": {"stringValue": cell_id}}],
        )
        if not scope.get("records"):
            return _resp({"error": "cell_not_found", "cell_id": cell_id}, 404)
        rec = scope["records"][0]
        old_value = float(rec[0].get("stringValue", "0"))
        local = rec[1].get("stringValue")
        period = rec[2].get("stringValue")
        column_name = rec[3].get("stringValue")
        package = rec[4].get("stringValue")
        period_id = rec[5].get("stringValue")
        version = rec[6].get("longValue", 1)
        zone = rec[7].get("stringValue") if not rec[7].get("isNull") else None

        actor = _actor(event)

        # Persist the override into the Aurora cell_corrections child table — the
        # legal/financial record (FK to cell + period, before/after, who/why,
        # versioned). The original kernel value stays intact in rate_cells;
        # corrections layer on top. (Replaces the DynamoDB overrides table.)
        rds.execute_statement(
            **common,
            sql=(
                "INSERT INTO cell_corrections (id, period_id, version, cell_id, "
                "  union_local, period, zone, package, column_name, kind, "
                "  prior_value, new_value, reason, actor, status) "
                "VALUES (:id::uuid, :pid::uuid, :ver, :cid::uuid, :local, :period, "
                "  :zone, :package, :col, 'override', :prior, :new, :reason, :actor, 'open')"
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
                {"name": "prior", "value": {"stringValue": str(old_value)}},
                {"name": "new", "value": {"stringValue": str(new_value_f)}},
                {"name": "reason", "value": {"stringValue": justification}},
                {"name": "actor", "value": {"stringValue": actor}},
            ],
        )

        # Audit-log entry so the Business activity tab shows it.
        rds.execute_statement(
            **common,
            sql=(
                "INSERT INTO audit_log (tenant, actor, action, details) "
                "VALUES ('laboraid', :actor, 'override', :details::jsonb)"
            ),
            parameters=[
                {"name": "actor", "value": {"stringValue": actor}},
                {
                    "name": "details",
                    "value": {
                        "stringValue": json.dumps({
                            "cell_id": cell_id,
                            "local": local,
                            "period": period,
                            "package": package,
                            "column_name": column_name,
                            "old_value": old_value,
                            "new_value": new_value_f,
                            "justification": justification,
                        })
                    },
                },
            ],
        )

        return _resp({
            "cell_id": cell_id,
            "status": "overridden",
            "old_value": old_value,
            "new_value": new_value_f,
            "actor": actor,
        })
    except Exception:
        logger.exception("cell-override failed")
        raise
