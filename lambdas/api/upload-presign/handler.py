"""Upload presign Lambda (Spec/09 §4 L2). Returns an S3 presigned PUT URL. Admins/Operations."""

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


def build_key(filename: str, prefix: str = "laboraid/uploads") -> str:
    """Build the S3 object key for an uploaded file."""
    return f"{prefix}/{os.path.basename(filename)}"


# Per-route Cognito group gate (Spec/09 §2.2, audit B3).
ALLOWED_GROUPS = ["Admins", "Operations"]


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        denied = authz.enforce_groups(event, ALLOWED_GROUPS)
        if denied:
            return denied
        body = json.loads(event.get("body") or "{}")
        key = build_key(body["filename"])
        import boto3

        url = boto3.client("s3").generate_presigned_url(
            "put_object",
            Params={"Bucket": os.environ["INPUTS_BUCKET"], "Key": key},
            ExpiresIn=900,
        )
        return _resp({"url": url, "key": key})
    except Exception:
        logger.exception("upload-presign failed")
        raise
