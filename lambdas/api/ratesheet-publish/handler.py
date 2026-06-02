"""Rate sheet publish Lambda (Spec/09 §4 L2). GATED: 409 unless approval_state='approved'."""

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


def _sub(event: dict[str, Any]) -> str:
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
        .get("sub", "unknown")
    )


def publish_guard(approval_state: str) -> tuple[int, dict[str, Any]]:
    """Publish guard (SOW contract). Returns (http_status, body).

    The publish endpoint MUST return HTTP 409 unless the rate period has been
    approved by the Business persona (Spec/09 §4 L2 §2.2, §4.4).
    """
    if approval_state != "approved":
        return 409, {"error": "not_approved", "approval_state": approval_state}
    return 200, {"approval_state": "published"}


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}")
        status, result = publish_guard(body.get("approval_state", "pending_review"))
        if status == 200:
            result["published_by"] = _sub(event)
        return _resp(result, status)
    except Exception:
        logger.exception("ratesheet-publish failed")
        raise
