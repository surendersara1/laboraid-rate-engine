"""Audit list Lambda (Spec/09 §4 L2).

Returns the global audit_log feed for Admins/Operations OR a per-user feed
for Business reviewers (filtered by actor = caller's email). Powers both the
admin audit page and the Business "My Activity" page.
"""

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
        "body": json.dumps(body, default=str),
    }


def _actor(event: dict[str, Any]) -> str:
    """Return a human-recognizable actor string from the JWT claims."""
    claims = (
        event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    )
    return (
        claims.get("email")
        or claims.get("cognito:username")
        or claims.get("sub")
        or "unknown"
    )


# Gate allows the global feed roles AND Business — the handler narrows the
# query to the caller's own rows when the caller is Business-only.
ALLOWED_GROUPS = ["Admins", "Operations", "Business"]


def _is_business_only(event: dict[str, Any]) -> bool:
    groups = set(authz.extract_groups(event))
    return "Business" in groups and not (groups & {"Admins", "Operations"})


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        denied = authz.enforce_groups(event, ALLOWED_GROUPS)
        if denied:
            return denied
        import boto3

        rds = boto3.client("rds-data")
        common = {
            "resourceArn": os.environ["AURORA_CLUSTER_ARN"],
            "secretArn": os.environ["AURORA_SECRET_ARN"],
            "database": "laboraid",
        }

        # Caller explicitly asks for their own feed via ?scope=me. Else default
        # to per-persona behavior: Business-only sees their own; Admin/Ops see
        # the global feed.
        qs = event.get("queryStringParameters") or {}
        force_me = (qs.get("scope") or "").lower() == "me"

        if force_me or _is_business_only(event):
            # My Activity feed — scope to the caller.
            caller = _actor(event)
            resp = rds.execute_statement(
                **common,
                sql=(
                    "SELECT id, to_char(ts AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"'), "
                    "       actor, action, COALESCE(details::text,'{}') "
                    "  FROM audit_log "
                    " WHERE actor = :actor "
                    " ORDER BY ts DESC LIMIT 200"
                ),
                parameters=[{"name": "actor", "value": {"stringValue": caller}}],
            )
            scope = "me"
        else:
            resp = rds.execute_statement(
                **common,
                sql=(
                    "SELECT id, to_char(ts AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"'), "
                    "       actor, action, COALESCE(details::text,'{}') "
                    "  FROM audit_log "
                    " ORDER BY ts DESC LIMIT 200"
                ),
            )
            scope = "all"

        records: list[dict[str, Any]] = []
        for row in resp.get("records", []):
            records.append({
                "id": row[0].get("longValue"),
                "ts": row[1].get("stringValue"),
                "actor": row[2].get("stringValue"),
                "action": row[3].get("stringValue"),
                "details": json.loads(row[4].get("stringValue") or "{}"),
            })
        return _resp({"scope": scope, "count": len(records), "records": records})
    except Exception:
        logger.exception("audit-list failed")
        raise
