"""Rate sheet reject Lambda (Spec/09 §4 L2). Business rejection; requires a reason."""

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


VALID_TAGS = {"missing_data", "wrong_extraction", "cba_mismatch", "other"}


def reject_transition(
    approval_state: str, reason: str, tags: list[str] | None = None
) -> tuple[int, dict[str, Any]]:
    """Decide the reject transition. Returns (http_status, body)."""
    if not reason or not reason.strip():
        return 422, {"error": "reason_required"}
    if approval_state == "published":
        return 409, {"error": "already_published"}
    bad = [t for t in (tags or []) if t not in VALID_TAGS]
    if bad:
        return 422, {"error": "invalid_tags", "invalid": bad}
    return 200, {"approval_state": "rejected", "rejection_reason": reason}


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}")
        status, result = reject_transition(
            body.get("approval_state", "pending_review"),
            body.get("reason", ""),
            body.get("tags"),
        )
        if status == 200:
            result["rejected_by"] = _sub(event)
        return _resp(result, status)
    except Exception:
        logger.exception("ratesheet-reject failed")
        raise
