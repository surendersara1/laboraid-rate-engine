"""Cell override Lambda (Spec/09 §4 L2). Writes a manual override to DDB. Business."""

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

        boto3.resource("dynamodb").Table(os.environ["OVERRIDES_TABLE"]).put_item(
            Item={
                "tenant#union#period": body["scope"],
                "cell_id#timestamp": f"{cell_id}#{body['timestamp']}",
                "value": body["value"],
                "actor": _sub(event),
            }
        )
        return _resp({"cell_id": cell_id, "status": "overridden"})
    except Exception:
        logger.exception("cell-override failed")
        raise
