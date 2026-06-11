"""Upload presign Lambda (Spec/09 §4 L2).

Returns an S3 presigned PUT URL for an uploaded file. Two extra behaviors
beyond the original simple presign:

1. **Batch grouping.** The browser generates a UUID per multi-select click
   (`batch_id`) and passes it here. We embed it in the S3 key so the
   pipeline can group related uploads:
       laboraid/uploads/<batch_id>/<filename>
   Downstream Lambdas (classifier, publisher, job-list) parse the key's
   3rd segment to recover the batch_id without needing a side-channel.

2. **Content-hash dedup.** Optional `content_hash` (SHA256 hex from the
   browser). When present we check the `file_hashes` DDB table; if this
   hash was already processed we return `{status: "duplicate", ...}`
   instead of a presigned URL, so the browser can skip the redundant
   upload + Bedrock cost.

Both fields are optional for backward compatibility with anything still
calling the original `{filename}` shape.
"""

from __future__ import annotations

import json
import os
import re
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


FILE_HASHES_TABLE = os.environ.get("FILE_HASHES_TABLE", "")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_BATCH_PERIOD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _coerce(v: Any) -> Any:
    """DDB Resource interface returns numbers as Decimal — json.dumps can't
    serialize that. Walk the value and coerce."""
    from decimal import Decimal

    if isinstance(v, Decimal):
        return int(v) if v == int(v) else float(v)
    if isinstance(v, dict):
        return {k: _coerce(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_coerce(x) for x in v]
    return v


def _resp(body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(_coerce(body)),
    }


def build_key(
    filename: str,
    batch_id: str | None = None,
    batch_period: str | None = None,
) -> str:
    """Build the S3 key. Layouts (most specific first):

    - ``laboraid/uploads/<batch_id>/<batch_period>/<filename>`` — when the
      browser detected an anchor period for the batch (Rate Notice in the
      batch had a clean YYYY.MM.DD filename). Downstream Lambdas read
      batch_period out of the key so multi-doc batches (CBA + Rate Notice +
      Apprentice Scale) all land in the SAME rate_period even though only
      one of those filenames carries the period.
    - ``laboraid/uploads/<batch_id>/<filename>`` — batched but no anchor
      detected (legacy multi-file shape).
    - ``laboraid/uploads/<filename>`` — single-file upload, no batch.
    """
    base = os.path.basename(filename)
    bid_ok = batch_id and _UUID_RE.match(batch_id)
    period_ok = batch_period and _BATCH_PERIOD_RE.match(batch_period)
    if bid_ok and period_ok:
        return f"laboraid/uploads/{batch_id}/{batch_period}/{base}"
    if bid_ok:
        return f"laboraid/uploads/{batch_id}/{base}"
    return f"laboraid/uploads/{base}"


def _lookup_hash(content_hash: str) -> dict[str, Any] | None:
    """Return the prior upload record if this content_hash was processed,
    else None. Errors swallowed — the dedup is a best-effort optimization,
    not a correctness gate."""
    if not FILE_HASHES_TABLE or not _HASH_RE.match(content_hash):
        return None
    try:
        import boto3

        item = boto3.resource("dynamodb").Table(FILE_HASHES_TABLE).get_item(
            Key={"content_hash": content_hash}
        )
        return item.get("Item")
    except Exception as e:  # pragma: no cover
        logger.warning("file_hashes lookup failed: %s", e)
        return None


# Per-route Cognito group gate (Spec/09 §2.2, audit B3).
ALLOWED_GROUPS = ["Admins", "Operations"]


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        denied = authz.enforce_groups(event, ALLOWED_GROUPS)
        if denied:
            return denied
        body = json.loads(event.get("body") or "{}")
        filename = body.get("filename") or ""
        if not filename:
            return _resp({"error": "filename_required"}, 400)
        batch_id = body.get("batch_id") or ""
        batch_period = (body.get("batch_period") or "").strip()
        content_hash = (body.get("content_hash") or "").lower()
        # force=true bypasses content-hash dedup: the reviewer explicitly
        # wants this PDF re-extracted (e.g. extraction logic improved since
        # the first run, or the prior run produced wrong values). We delete
        # the prior hash row so the fresh row inserted below wins, and the
        # re-run is audit-visible via the new batch_id.
        force = bool(body.get("force"))

        # Content-hash dedup. If we've already seen this exact byte content
        # processed to completion, skip — give the caller the existing
        # period info so the UI can route them straight to that rate sheet.
        if content_hash:
            prior = _lookup_hash(content_hash)
            if prior and not force:
                logger.info(
                    "upload-presign: dedup hit for %s — skipping new upload",
                    content_hash[:12],
                )
                return _resp({
                    "status": "duplicate",
                    "content_hash": content_hash,
                    "existing_period_id": prior.get("period_id"),
                    "existing_s3_key": prior.get("s3_key"),
                    "first_seen_at": prior.get("first_seen_at"),
                })
            if prior and force:
                logger.info(
                    "upload-presign: FORCE reprocess for %s (prior period %s)",
                    content_hash[:12], prior.get("period_id"),
                )
                try:
                    import boto3 as _b
                    _b.resource("dynamodb").Table(FILE_HASHES_TABLE).delete_item(
                        Key={"content_hash": content_hash}
                    )
                except Exception as e:  # pragma: no cover
                    logger.warning("force: prior hash delete failed: %s", e)

        key = build_key(filename, batch_id=batch_id, batch_period=batch_period)
        import boto3

        # Record the (content_hash → s3_key) mapping NOW so downstream
        # Publisher doesn't need to read it back from S3 object metadata
        # (the browser would have to send x-amz-meta-content-hash as a
        # signed header on the PUT, which complicates the presign and
        # leaks signing details into the UI). Storing pre-PUT means:
        #  * If the PUT never happens or the pipeline fails, the row
        #    points at an s3_key that doesn't have a period yet — that
        #    just means dedup lookups return without an existing_period_id
        #    and we proceed with a fresh upload.
        #  * Publisher backfills period_id when the row reaches Aurora.
        if content_hash and FILE_HASHES_TABLE:
            try:
                import time as _time

                boto3.resource("dynamodb").Table(FILE_HASHES_TABLE).put_item(
                    Item={
                        "content_hash": content_hash,
                        "s3_key": key,
                        "batch_id": batch_id or None,
                        "first_seen_at": int(_time.time()),
                    },
                    ConditionExpression="attribute_not_exists(content_hash)",
                )
            except boto3.client("dynamodb").exceptions.ConditionalCheckFailedException:
                # Another caller raced us to insert the same hash. Re-check
                # — if the prior row already points at a period, return
                # duplicate. Otherwise proceed (let the pipeline finish).
                prior = _lookup_hash(content_hash)
                if prior and prior.get("period_id"):
                    return _resp({
                        "status": "duplicate",
                        "content_hash": content_hash,
                        "existing_period_id": prior.get("period_id"),
                        "existing_s3_key": prior.get("s3_key"),
                        "first_seen_at": prior.get("first_seen_at"),
                    })
            except Exception as e:  # pragma: no cover
                logger.warning("file_hashes pre-put failed: %s", e)

        url = boto3.client("s3").generate_presigned_url(
            "put_object",
            Params={"Bucket": os.environ["INPUTS_BUCKET"], "Key": key},
            ExpiresIn=900,
        )
        return _resp({
            "status": "ready",
            "url": url,
            "key": key,
            "batch_id": batch_id or None,
            "batch_period": batch_period or None,
        })
    except Exception:
        logger.exception("upload-presign failed")
        raise
