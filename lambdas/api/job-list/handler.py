"""Job list Lambda — Step Functions execution feed (Admins/Operations).

Reads from the main rate-engine state machine so the admin Jobs page shows
every PDF upload that triggered a run, with status + duration + the union
and period the run is extracting (parsed from the EventBridge input).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import authz  # shared Lambda layer (/opt/python/authz.py)

# Same filename regex the classifier uses. Lets us recover (local, period)
# for UI-uploaded files whose S3 key is the flat `laboraid/uploads/...`
# layout instead of the canonical `laboraid/<Trade>/<Local>/...`.
_FILENAME_RE = re.compile(
    r"(?P<date>\d{4}\.\d{2}\.\d{2})\.(?P<local>\d{3})\s+(?P<doc>.+?)\.pdf$",
    re.IGNORECASE,
)
_BATCH_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_BATCH_PERIOD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# CBA range-date filename (matches classifier _FILENAME_RANGE).
_FILENAME_RANGE_RE = re.compile(
    r"(?P<sd>\d{4}\.\d{2}\.\d{2})[-–]"
    r"(?P<ed>\d{4}\.\d{2}\.\d{2})\.(?P<local>\d{3})\s+(?P<doc>.+?)\.pdf$",
    re.IGNORECASE,
)

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


def _key_from_event_input(raw_input: str) -> tuple[str, str, str, str | None]:
    """Pull (s3_key, union_local, period, batch_id) out of the SFN execution
    input.

    Handles four uploads layouts plus the canonical kernel path:

    - ``laboraid/<Trade>/<Local>/<Period>/<file>`` — canonical kernel path.
    - ``laboraid/uploads/<batch_id>/<YYYY-MM-DD>/<file>`` — multi-doc upload
      with a browser-detected anchor period (covers CBA + Rate Notice etc.).
    - ``laboraid/uploads/<batch_id>/<file>`` — batched, no anchor period.
    - ``laboraid/uploads/<file>`` — single-file legacy upload.

    For the uploads layouts the union/period come from the filename via the
    same regexes the classifier uses; if the filename is a CBA-style range
    we fall back to the batch_period segment of the S3 key.
    """
    try:
        evt = json.loads(raw_input)
        s3_key = evt.get("detail", {}).get("object", {}).get("key", "") or ""
        union = period = ""
        batch_id: str | None = None
        if not s3_key:
            return "", "", "", None
        parts = s3_key.split("/")

        # Canonical kernel path
        if (
            len(parts) >= 5
            and parts[0] == "laboraid"
            and parts[1].lower() != "uploads"
        ):
            union = f"{parts[1]} {parts[2]}"
            period = parts[3]
            return s3_key, union, period, None

        # uploads/<batch_id>/<batch_period>/<file>
        if (
            len(parts) >= 5
            and parts[0] == "laboraid"
            and parts[1] == "uploads"
            and _BATCH_ID_RE.match(parts[2] or "")
            and _BATCH_PERIOD_RE.match(parts[3] or "")
        ):
            batch_id = parts[2]
            period = parts[3]  # batch-anchored period wins for non-rate-notice docs
            filename = parts[4]
        # uploads/<batch_id>/<file>
        elif (
            len(parts) >= 4
            and parts[0] == "laboraid"
            and parts[1] == "uploads"
            and _BATCH_ID_RE.match(parts[2] or "")
        ):
            batch_id = parts[2]
            filename = parts[3]
        elif len(parts) >= 3 and parts[0] == "laboraid" and parts[1] == "uploads":
            filename = parts[2]
        else:
            filename = parts[-1]

        m = _FILENAME_RE.search(filename)
        if m:
            local = m.group("local")
            union = f"Local {local}"
            if not period:
                period = m.group("date").replace(".", "-")
            else:
                # For Rate Notice files the filename date IS the period;
                # for any other doc-shape in the same batch (CBA/scale) the
                # batch_period from the key already wins, so leave `period`.
                doc = m.group("doc").lower()
                if "rate notice" in doc or "rate sheet" in doc or "wage sheet" in doc:
                    period = m.group("date").replace(".", "-")
        else:
            rm = _FILENAME_RANGE_RE.search(filename)
            if rm:
                local = rm.group("local")
                union = f"Local {local}"
                # period already set from batch_period segment if present;
                # otherwise use the range's start date as a last resort.
                if not period:
                    period = rm.group("sd").replace(".", "-")
        return s3_key, union, period, batch_id
    except Exception:
        return "", "", "", None


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
                s3_key, union, period, batch_id = _key_from_event_input(
                    desc.get("input", "{}")
                )
            except Exception:
                s3_key = union = period = ""
                batch_id = None
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
                "batch_id": batch_id,
            })

        return _resp({"jobs": jobs, "count": len(jobs)})
    except Exception:
        logger.exception("job-list failed")
        raise
