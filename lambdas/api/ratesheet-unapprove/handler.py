"""Rate sheet unapprove Lambda (Spec/09 §4 L2). Original approver only, before publish."""

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


def unapprove_transition(
    approval_state: str, requester_sub: str, approver_sub: str
) -> tuple[int, dict[str, Any]]:
    """Decide the unapprove transition. Returns (http_status, body)."""
    if approval_state == "published":
        return 409, {"error": "already_published"}
    if approval_state != "approved":
        return 409, {"error": "not_approved", "approval_state": approval_state}
    if requester_sub != approver_sub:
        return 403, {"error": "not_original_approver"}
    return 200, {"approval_state": "pending_review"}


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}")
        status, result = unapprove_transition(
            body.get("approval_state", "approved"),
            _sub(event),
            body.get("approved_by", ""),
        )
        return _resp(result, status)
    except Exception:
        logger.exception("ratesheet-unapprove failed")
        raise
