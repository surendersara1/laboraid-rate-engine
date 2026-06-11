"""Batch-process API Lambda (POST /v1/batches/process).

This is the ONLY pipeline trigger now. The old per-S3-object EventBridge rule
is disabled — uploading a PDF no longer starts anything. The reviewer stages
all PDFs for one rate period, presses "Process this batch", and the browser
calls this endpoint with the full manifest. We start ONE Step Functions
execution that processes every doc SEQUENTIALLY in the planner's order
(CBA first, then by effective date), so there is no parallel race and no
duplicate cells.

Request body:
  {
    "batch_id": "<uuid>",
    "batch_period": "2026-01-01",
    "files": [{"s3_key": "...", "filename": "..."}, ...]
  }

Response:
  { "status": "started", "execution_arn": "...", "doc_count": N }
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import authz  # shared Lambda layer (/opt/python/authz.py)

try:  # pragma: no cover
    from aws_lambda_powertools import Logger, Tracer

    logger = Logger(service="laboraid-api")
    tracer = Tracer()

    def _instrument(fn: Any) -> Any:
        return logger.inject_lambda_context(tracer.capture_lambda_handler(fn))

except ModuleNotFoundError:  # pragma: no cover
    import logging

    logger = logging.getLogger("laboraid-api")

    def _instrument(fn: Any) -> Any:
        return fn


STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")
ALLOWED_GROUPS = ["Admins", "Operations", "Business"]
_NAME_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def _resp(body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        denied = authz.enforce_groups(event, ALLOWED_GROUPS)
        if denied:
            return denied
        body = json.loads(event.get("body") or "{}")
        batch_id = (body.get("batch_id") or "").strip()
        batch_period = (body.get("batch_period") or "").strip()
        files = body.get("files") or []
        if not files:
            return _resp({"error": "files_required"}, 400)
        if not STATE_MACHINE_ARN:
            return _resp({"error": "state_machine_not_configured"}, 500)

        import boto3

        sfn = boto3.client("stepfunctions")
        # Deterministic-ish execution name (SFN requires unique per 90 days):
        # batch_id is already a uuid; suffix with file count to disambiguate
        # re-processes of the same batch.
        raw_name = f"batch-{batch_id}-{len(files)}"
        exec_name = _NAME_SAFE.sub("-", raw_name)[:80]
        payload = {
            "batch_id": batch_id,
            "batch_period": batch_period,
            "files": [
                {"s3_key": f.get("s3_key") or f.get("key") or "",
                 "filename": f.get("filename") or ""}
                for f in files
            ],
        }
        try:
            resp = sfn.start_execution(
                stateMachineArn=STATE_MACHINE_ARN,
                name=exec_name,
                input=json.dumps(payload),
            )
        except sfn.exceptions.ExecutionAlreadyExists:
            # Same batch+count already processing — make the name unique.
            import time

            resp = sfn.start_execution(
                stateMachineArn=STATE_MACHINE_ARN,
                name=f"{exec_name[:70]}-{int(time.time())}"[:80],
                input=json.dumps(payload),
            )
        logger.info(
            "batch-process: started %s for batch=%s (%d files)",
            resp["executionArn"], batch_id, len(files),
        )
        return _resp({
            "status": "started",
            "execution_arn": resp["executionArn"],
            "doc_count": len(files),
        })
    except Exception:
        logger.exception("batch-process failed")
        raise
