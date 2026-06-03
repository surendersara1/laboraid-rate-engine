"""Profile list Lambda (Spec/09 §4 L2). Lists configured unions. Cognito."""

from __future__ import annotations

import json
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


KNOWN_UNIONS = [
    "pipe_fitters_537",
    "sprinkler_fitters_483",
    "sprinkler_fitters_704",
    "sprinkler_fitters_281",
    "sprinkler_fitters_821",
]


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        return _resp({"unions": KNOWN_UNIONS, "count": len(KNOWN_UNIONS)})
    except Exception:
        logger.exception("profile-list failed")
        raise
