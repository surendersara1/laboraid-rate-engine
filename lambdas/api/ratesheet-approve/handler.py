"""Rate sheet approve Lambda (Spec/09 §4 L2). Business sign-off; requires empty review queue."""

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


def approve_transition(
    approval_state: str, review_queue_empty: bool
) -> tuple[int, dict[str, Any]]:
    """Decide the approve transition. Returns (http_status, body)."""
    if not review_queue_empty:
        return 422, {"error": "review_queue_not_empty"}
    if approval_state not in ("pending_review", "rejected"):
        return 409, {"error": "not_approvable", "approval_state": approval_state}
    return 200, {"approval_state": "approved"}


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}")
        status, result = approve_transition(
            body.get("approval_state", "pending_review"),
            bool(body.get("review_queue_empty", False)),
        )
        if status == 200:
            result["approved_by"] = _sub(event)
        return _resp(result, status)
    except Exception:
        logger.exception("ratesheet-approve failed")
        raise
