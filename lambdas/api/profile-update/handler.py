"""Profile update Lambda (Spec/09 §4 L2). Writes the union's profile to Aurora
(unions.profile_yaml) — the system of record, editable from the Admin Profiles
tab."""

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


# Per-route Cognito group gate (Spec/09 §2.2, audit B3).
ALLOWED_GROUPS = ["Admins"]


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        denied = authz.enforce_groups(event, ALLOWED_GROUPS)
        if denied:
            return denied
        local = event["pathParameters"]["local"]
        body = json.loads(event.get("body") or "{}")
        raw = body.get("profile_yaml", body.get("profile"))
        if raw is None:
            return _resp({"error": "profile_yaml required"}, 400)
        # Accept either a JSON object or a JSON/YAML string; store canonical JSON.
        profile = raw if isinstance(raw, dict) else json.loads(raw)
        version = body.get("profile_version") or "edited"

        import boto3

        rds = boto3.client("rds-data")
        rds.execute_statement(
            resourceArn=os.environ["AURORA_CLUSTER_ARN"],
            secretArn=os.environ["AURORA_SECRET_ARN"],
            database="laboraid",
            sql="UPDATE unions SET profile_yaml = :p::jsonb, profile_version = :v "
                "WHERE local = :l::int",
            parameters=[
                {"name": "p", "value": {"stringValue": json.dumps(profile)}},
                {"name": "v", "value": {"stringValue": str(version)}},
                {"name": "l", "value": {"stringValue": str(local)}},
            ],
        )
        logger.info("profile-update: saved profile for local=%s", local)
        return _resp({"local": local, "status": "saved", "profile_version": version})
    except Exception:
        logger.exception("profile-update failed")
        raise
