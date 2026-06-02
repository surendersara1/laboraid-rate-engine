"""Agent toggle Lambda (Spec/09 §4 L2 + L3 §3.2). PATCH enabled on agent-config. Admins only."""

from __future__ import annotations

import json
import os
from typing import Any

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


def build_update(enabled: bool, updated_by: str) -> dict[str, Any]:
    """Build the agent-config update for an enable/disable toggle."""
    return {
        "UpdateExpression": "SET enabled = :e, updated_by = :u",
        "ExpressionAttributeValues": {":e": enabled, ":u": updated_by},
    }


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        name = event["pathParameters"]["name"]
        body = json.loads(event.get("body") or "{}")
        enabled = bool(body.get("enabled", True))
        actor = (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("jwt", {})
            .get("claims", {})
            .get("sub", "unknown")
        )
        import boto3

        boto3.resource("dynamodb").Table(os.environ["AGENT_CONFIG_TABLE"]).update_item(
            Key={"agent_name": name}, **build_update(enabled, actor)
        )
        return _resp({"agent_name": name, "enabled": enabled})
    except Exception:
        logger.exception("agent-toggle failed")
        raise
