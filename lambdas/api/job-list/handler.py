"""Job list Lambda — reads the jobs DynamoDB read-model (Admins/Operations).

WAS: live Step Functions ListExecutions + N x DescribeExecution per page load
(N+1, slow, and capped at SFN's 90-day history). NOW: one indexed query on the
`jobs` table's `by-recency` GSI, populated by the job-writer from Step Functions
state-change events. Fast, paginated, unbounded history.
"""

from __future__ import annotations

import base64
import json
import os
from decimal import Decimal
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


ALLOWED_GROUPS = ["Admins", "Operations"]


def _resp(body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _num(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v % 1 == 0 else float(v)
    return v


def _job_view(it: dict[str, Any]) -> dict[str, Any]:
    """Map a jobs-table item to the UI's job shape (pre-resolved by job-writer)."""
    keys = it.get("source_keys") or []
    return {
        "job_id": it.get("job_id"),
        "execution_arn": it.get("execution_arn"),
        "status": it.get("status"),
        "started_at": it.get("started_at"),
        "stopped_at": it.get("stopped_at"),
        "duration_ms": _num(it.get("duration_ms")),
        "union": it.get("union"),
        "local": it.get("local"),
        "period": it.get("period"),
        "batch_id": it.get("batch_id"),
        "source_keys": keys,
        "source_s3_key": keys[0] if keys else "",
        "file_count": len(keys),
        "row_count": _num(it.get("row_count")),
        "cell_count": _num(it.get("cell_count")),
        "error": it.get("error"),
    }


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        denied = authz.enforce_groups(event, ALLOWED_GROUPS)
        if denied:
            return denied
        import boto3
        from boto3.dynamodb.conditions import Attr, Key

        table = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])
        params = event.get("queryStringParameters") or {}
        status_filter = params.get("status")
        try:
            limit = max(1, min(int(params.get("limit") or 100), 200))
        except (TypeError, ValueError):
            limit = 100

        kw: dict[str, Any] = {
            "IndexName": "by-recency",
            "KeyConditionExpression": Key("gsi1pk").eq("JOB"),
            "ScanIndexForward": False,  # newest first
            "Limit": limit,
        }
        if status_filter:
            kw["FilterExpression"] = Attr("status").eq(status_filter)
        cursor = params.get("cursor")
        if cursor:
            try:
                kw["ExclusiveStartKey"] = json.loads(base64.b64decode(cursor).decode())
            except Exception:  # pragma: no cover - bad cursor -> start at top
                pass

        res = table.query(**kw)
        jobs = [_job_view(it) for it in res.get("Items", [])]
        next_cursor = None
        if res.get("LastEvaluatedKey"):
            next_cursor = base64.b64encode(
                json.dumps(res["LastEvaluatedKey"], default=str).encode()
            ).decode()
        return _resp({"jobs": jobs, "count": len(jobs), "next_cursor": next_cursor})
    except Exception:
        logger.exception("job-list failed")
        raise
