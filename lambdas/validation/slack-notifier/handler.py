"""Slack-notifier Lambda (Spec/09 §4 L6 §6.1).

Subscribed to the failures + review-needed SNS topics; formats each structured
event (§6.3) into a Slack message and POSTs it to the incoming-webhook URL stored
in Secrets Manager. Pure message formatting is unit-testable; the webhook fetch +
POST happen only in `handler`.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

try:  # pragma: no cover - present in the Lambda runtime
    from aws_lambda_powertools import Logger, Tracer

    logger = Logger(service="laboraid-notifications")
    tracer = Tracer()

    def _instrument(fn: Any) -> Any:
        return logger.inject_lambda_context(tracer.capture_lambda_handler(fn))

except ModuleNotFoundError:  # pragma: no cover - offline unit-test env
    import logging

    logger = logging.getLogger("laboraid-notifications")  # type: ignore[assignment]

    def _instrument(fn: Any) -> Any:
        return fn


_EMOJI = {
    "laboraid.job.failed": ":red_circle:",
    "laboraid.rate-sheet.review-needed": ":eyes:",
    "laboraid.rate-sheet.published": ":white_check_mark:",
}


def format_slack_message(event: dict[str, Any]) -> dict[str, Any]:
    """Build a Slack chat.postMessage-style payload from a structured event."""
    name = event.get("event", "laboraid.event")
    emoji = _EMOJI.get(name, ":information_source:")
    parts = [f"{emoji} *{name}*"]
    if "union_local" in event:
        parts.append(f"union {event['union_local']}")
    if "period" in event:
        parts.append(f"period {event['period']}")
    if "stage" in event:
        parts.append(f"stage `{event['stage']}`")
    err = event.get("error", {})
    if err:
        parts.append(f"\n> {err.get('type', 'Error')}: {err.get('message', '')}")
    links = event.get("links", {})
    if links.get("review_url"):
        parts.append(f"\n<{links['review_url']}|Open review>")
    return {"text": " · ".join(p for p in parts if p)}


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """SNS event → Slack post. Webhook URL comes from Secrets Manager."""
    try:
        import boto3

        secret_name = os.environ["SLACK_WEBHOOK_SECRET"]
        webhook = boto3.client("secretsmanager").get_secret_value(SecretId=secret_name)[
            "SecretString"
        ]
        posted = 0
        for record in event.get("Records", []):
            message = json.loads(record["Sns"]["Message"])
            payload = format_slack_message(message)
            req = urllib.request.Request(
                webhook,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)  # noqa: S310 (trusted webhook URL)
            posted += 1
        logger.info("posted %d slack messages", posted)
        return {"posted": posted}
    except Exception:
        logger.exception("slack notifier failed")
        raise
