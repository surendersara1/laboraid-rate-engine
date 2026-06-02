"""Agent list Lambda (Spec/09 §4 L2). Reads agent-config DDB. Admins/Operations."""

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


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        import boto3

        items = (
            boto3.resource("dynamodb").Table(os.environ["AGENT_CONFIG_TABLE"]).scan()
        )
        return _resp({"agents": items.get("Items", []), "count": items.get("Count", 0)})
    except Exception:
        logger.exception("agent-list failed")
        raise
