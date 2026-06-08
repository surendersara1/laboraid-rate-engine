"""Job list Lambda — Step Functions execution feed (Admins/Operations).

Reads from the main rate-engine state machine so the admin Jobs page shows
every PDF upload that triggered a run, with status + duration + the union
and period the run is extracting (parsed from the EventBridge input).
"""

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
        "body": json.dumps(body, default=str),
    }


# Per-route Cognito group gate (Spec/09 §2.2, audit B3).
ALLOWED_GROUPS = ["Admins", "Operations"]

STATE_MACHINE_ARN = os.environ.get(
    "STATE_MACHINE_ARN",
    "arn:aws:states:us-east-2:908106425069:stateMachine:laboraid-dev-l3-sfn-main",
)


def _key_from_event_input(raw_input: str) -> tuple[str, str, str]:
    """Pull (s3_key, union_local, period) out of the SFN execution input.

    Best-effort: the input is the EventBridge S3-Object-Created payload; if
    the shape differs we return empty strings rather than crashing the list.
    """
    try:
        evt = json.loads(raw_input)
        s3_key = evt.get("detail", {}).get("object", {}).get("key", "") or ""
        union = period = ""
        if s3_key:
            parts = s3_key.split("/")
            # laboraid/<Trade>/<Local>/<Period>/<file>
            if len(parts) >= 5 and parts[0] == "laboraid":
                union = f"{parts[1]} {parts[2]}"
                period = parts[3]
        return s3_key, union, period
    except Exception:
        return "", "", ""


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        denied = authz.enforce_groups(event, ALLOWED_GROUPS)
        if denied:
            return denied
        import boto3

        sfn = boto3.client("stepfunctions")
        params = event.get("queryStringParameters") or {}
        status_filter = params.get("status")  # RUNNING|SUCCEEDED|FAILED|ABORTED|TIMED_OUT

        kw: dict[str, Any] = {"stateMachineArn": STATE_MACHINE_ARN, "maxResults": 100}
        if status_filter:
            kw["statusFilter"] = status_filter
        execs = sfn.list_executions(**kw)["executions"]

        # For each execution, pull the input to extract union/period (one extra
        # describe per exec — cheap and reliable). Capped at 100 above.
        jobs: list[dict[str, Any]] = []
        for e in execs:
            try:
                desc = sfn.describe_execution(executionArn=e["executionArn"])
                s3_key, union, period = _key_from_event_input(desc.get("input", "{}"))
            except Exception:
                s3_key = union = period = ""
            start = e.get("startDate")
            stop = e.get("stopDate")
            duration_ms = (
                int((stop - start).total_seconds() * 1000) if (start and stop) else None
            )
            jobs.append({
                "job_id": e["name"],
                "execution_arn": e["executionArn"],
                "status": e["status"],
                "started_at": start.isoformat() if start else None,
                "stopped_at": stop.isoformat() if stop else None,
                "duration_ms": duration_ms,
                "union": union,
                "period": period,
                "source_s3_key": s3_key,
            })

        return _resp({"jobs": jobs, "count": len(jobs)})
    except Exception:
        logger.exception("job-list failed")
        raise
