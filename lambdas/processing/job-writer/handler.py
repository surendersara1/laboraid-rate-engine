"""job-writer Lambda — projects Step Functions execution state into the `jobs`
DynamoDB table (the dashboard read-model).

Two entry paths, same write logic:
- EventBridge "Step Functions Execution Status Change" events (live, per state change).
- `{"backfill": true}` invocation — replays ALL existing executions into the table
  once, so the dashboard shows history immediately and survives SFN's 90-day cliff.

The expensive union/period/artifact resolution happens ONCE here, never on a
dashboard load. Idempotent: put_item overwrites with the latest state.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import boto3

try:  # pragma: no cover - present in the Lambda runtime
    from aws_lambda_powertools import Logger

    logger = Logger(service="laboraid-job-writer")
except ModuleNotFoundError:  # pragma: no cover - offline
    import logging

    logger = logging.getLogger("laboraid-job-writer")  # type: ignore[assignment]

_ddb = boto3.resource("dynamodb")
_table = _ddb.Table(os.environ["JOBS_TABLE"])
_sfn = boto3.client("stepfunctions")
_SM_ARN = os.environ.get("STATE_MACHINE_ARN", "")

_UNION_DISPLAY = {
    "537": "Pipefitters 537",
    "704": "Sprinkler Fitters 704",
    "821": "Sprinkler Fitters 821",
    "483": "Sprinkler Fitters 483",
    "281": "Sprinkler Fitters 281",
    "12": "Pipefitters 12",
    "709": "Sprinkler Fitters 709",
}


def _iso(ts: Any) -> str | None:
    """Accept epoch-millis (EventBridge) OR a datetime (describe) -> ISO-8601 UTC."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc).isoformat()
    try:
        return datetime.fromtimestamp(float(ts) / 1000.0, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _local_from_filename(fn: str) -> str | None:
    m = re.search(r"\b(\d{2,4})\b", fn or "")
    return m.group(1) if m else None


def _resolve(input_s: str, output_s: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        inp = json.loads(input_s) if input_s else {}
    except (TypeError, ValueError):
        inp = {}
    files = inp.get("files") or []
    out["source_keys"] = [f.get("s3_key") for f in files if f.get("s3_key")]
    out["source_files"] = [f.get("filename") for f in files if f.get("filename")]
    out["batch_id"] = inp.get("batch_id")
    out["period"] = inp.get("batch_period")
    for f in files:
        loc = _local_from_filename(f.get("filename", ""))
        if loc:
            out["local"] = loc
            break
    try:
        o = json.loads(output_s) if output_s else {}
    except (TypeError, ValueError):
        o = {}
    if o:
        out["local"] = str(o.get("local") or out.get("local") or "")
        out["period"] = o.get("period") or out.get("period")
        out["row_count"] = o.get("row_count")
        out["cell_count"] = o.get("cell_count")
        out["output_csv"] = o.get("output_csv")
        out["output_xlsx"] = o.get("output_xlsx")
        out["period_id"] = o.get("period_id")
        out["trace"] = o.get("trace")  # pipeline calls (Aurora/S3/Bedrock)
    loc = out.get("local")
    out["union"] = _UNION_DISPLAY.get(str(loc), f"Local {loc}" if loc else "—")
    return {k: v for k, v in out.items() if v not in (None, [], "")}


def _write(
    name: str,
    status: str,
    started: Any,
    stopped: Any,
    input_s: str | None,
    output_s: str | None,
    exec_arn: str | None,
    error: str | None,
) -> None:
    started_iso = _iso(started)
    stopped_iso = _iso(stopped)
    dur = None
    if isinstance(started, datetime) and isinstance(stopped, datetime):
        dur = int((stopped - started).total_seconds() * 1000)
    elif isinstance(started, (int, float)) and isinstance(stopped, (int, float)):
        dur = int(stopped - started)
    item: dict[str, Any] = {
        "job_id": name,
        "gsi1pk": "JOB",
        "status": status,
        "started_at": started_iso or datetime.now(timezone.utc).isoformat(),
        "execution_arn": exec_arn,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if stopped_iso:
        item["stopped_at"] = stopped_iso
    if dur is not None:
        item["duration_ms"] = dur
    if status in ("FAILED", "TIMED_OUT", "ABORTED") and error:
        item["error"] = error
    item.update(_resolve(input_s or "{}", output_s))
    # Derived 3-stage timeline (no SFN history needed for the detail view).
    stages = ["Plan", "Synthesize", "SynthPublish"]
    if status == "SUCCEEDED":
        item["timeline"] = [{"step": s, "status": "SUCCEEDED"} for s in stages]
    elif status == "RUNNING":
        item["timeline"] = [
            {"step": s, "status": "RUNNING" if i == 0 else "PENDING"}
            for i, s in enumerate(stages)
        ]
    else:
        item["timeline"] = [{"step": s, "status": status} for s in stages]
    _table.put_item(Item={k: v for k, v in item.items() if v is not None})


def _backfill() -> dict[str, Any]:
    if not _SM_ARN:
        return {"error": "STATE_MACHINE_ARN not set"}
    n = 0
    token: str | None = None
    while True:
        kw: dict[str, Any] = {"stateMachineArn": _SM_ARN, "maxResults": 100}
        if token:
            kw["nextToken"] = token
        page = _sfn.list_executions(**kw)
        for e in page["executions"]:
            try:
                d = _sfn.describe_execution(executionArn=e["executionArn"])
                _write(
                    d["name"], d["status"], d.get("startDate"), d.get("stopDate"),
                    d.get("input"), d.get("output"), d["executionArn"],
                    d.get("error") or d.get("cause"),
                )
                n += 1
            except Exception:  # pragma: no cover
                logger.exception("backfill: failed for %s", e.get("name"))
        token = page.get("nextToken")
        if not token:
            break
    logger.info("backfill: wrote %d jobs", n)
    return {"backfilled": n}


def handler(event: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
    if event.get("backfill"):
        return _backfill()

    detail = event.get("detail") or {}
    name, status = detail.get("name"), detail.get("status")
    if not name or not status:
        logger.warning("job-writer: event missing name/status; skipping")
        return {"skipped": True}
    input_s, output_s = detail.get("input"), detail.get("output")
    if input_s is None and detail.get("executionArn"):
        try:
            d = _sfn.describe_execution(executionArn=detail["executionArn"])
            input_s, output_s = d.get("input"), d.get("output")
        except Exception:  # pragma: no cover
            logger.exception("job-writer: describe fallback failed")
    _write(
        name, status, detail.get("startDate"), detail.get("stopDate"),
        input_s, output_s, detail.get("executionArn"),
        detail.get("error") or detail.get("cause"),
    )
    logger.info("job-writer: wrote %s status=%s", name, status)
    return {"job_id": name, "status": status}
