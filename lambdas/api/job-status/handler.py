"""Job status Lambda — Step Functions execution detail (Admins/Operations).

Returns the full execution history collapsed into per-state durations + the
input/output artifacts (source PDF, output CSV) so the admin Job Detail page
can render a timeline + artifact links + a clickable jump to the agent log.
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


ALLOWED_GROUPS = ["Admins", "Operations"]
STATE_MACHINE_ARN = os.environ.get(
    "STATE_MACHINE_ARN",
    "arn:aws:states:us-east-2:908106425069:stateMachine:laboraid-dev-l3-sfn-main",
)
INPUTS_BUCKET = os.environ.get("INPUTS_BUCKET", "laboraid-dev-l3-bucket-inputs")
OUTPUTS_BUCKET = os.environ.get("OUTPUTS_BUCKET", "laboraid-dev-l3-bucket-outputs")
AGENT_RUNTIME_ID = os.environ.get(
    "AGENT_RUNTIME_ID", "laboraid_dev_l5_agent_extractor-yYd9gFA7LZ"
)


def _collapse_history(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce raw SFN history into one row per state with start/end + status,
    including the Lambda resource ARN, the input the Lambda was invoked with,
    and the output it returned (or its error cause if it failed). Inputs and
    outputs are clipped to keep responses fast.
    """
    rows: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    last_running: list[str] = []
    for e in events:
        et = e["type"]
        if et.endswith("StateEntered"):
            name = e["stateEnteredEventDetails"]["name"]
            if name not in rows:
                rows[name] = {
                    "name": name,
                    "entered_at": e["timestamp"],
                    "exited_at": None,
                    "duration_ms": None,
                    "status": "running",
                    "error": None,
                    "cause": None,
                    "input": (e["stateEnteredEventDetails"].get("input") or "")[:4000],
                    "output": None,
                    "resource": None,
                }
                order.append(name)
            last_running.append(name)
        elif et.endswith("StateExited"):
            name = e["stateExitedEventDetails"]["name"]
            r = rows.get(name)
            if r:
                r["exited_at"] = e["timestamp"]
                if r["entered_at"]:
                    delta = (e["timestamp"] - r["entered_at"]).total_seconds() * 1000
                    r["duration_ms"] = int(delta)
                if r["status"] == "running":
                    r["status"] = "ok"
                if not r.get("output"):
                    r["output"] = (
                        e["stateExitedEventDetails"].get("output") or ""
                    )[:4000]
        elif et == "LambdaFunctionScheduled":
            arn = (e.get("lambdaFunctionScheduledEventDetails") or {}).get("resource")
            if last_running and arn:
                rows[last_running[-1]]["resource"] = arn
        elif et == "TaskScheduled":
            res = (e.get("taskScheduledEventDetails") or {}).get("resource")
            if last_running and res:
                rows[last_running[-1]]["resource"] = res
        elif "Failed" in et:
            details_key = next(
                (k for k in e if k.endswith("FailedEventDetails")),
                None,
            )
            err = (e.get(details_key) or {}).get("error", "Failed")
            cause = ((e.get(details_key) or {}).get("cause", "") or "")[:1000]
            for n in reversed(order):
                if rows[n]["status"] == "running":
                    rows[n]["status"] = "failed"
                    rows[n]["error"] = err
                    rows[n]["cause"] = cause
                    break

    # Compute a CloudWatch Logs link for each step that ran a Lambda or
    # references the AgentCore runtime, so the UI can deep-link to logs.
    for name, r in rows.items():
        res = r.get("resource") or ""
        # Lambda function ARN → /aws/lambda/<name>
        if res.startswith("arn:aws:lambda:"):
            fn = res.split(":function:")[-1].split(":")[0]
            r["log_group"] = f"/aws/lambda/{fn}"
        # AgentCore InvokeAgentRuntime — point at the runtime log group
        elif "InvokeAgentRuntime" in res or name == "ExtractViaAgent":
            r["log_group"] = (
                "/aws/bedrock-agentcore/runtimes/"
                "laboraid_dev_l5_agent_extractor-yYd9gFA7LZ-DEFAULT"
            )

    return [rows[n] for n in order]


def _presign(s3: Any, bucket: str, key: str) -> str | None:
    if not key:
        return None
    try:
        return s3.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=3600
        )
    except Exception:
        return None


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        denied = authz.enforce_groups(event, ALLOWED_GROUPS)
        if denied:
            return denied
        job_id = event["pathParameters"]["id"]
        import boto3

        sfn = boto3.client("stepfunctions")
        s3 = boto3.client("s3")

        # Build the execution ARN deterministically from the state-machine ARN
        # + the job id (which IS the SFN execution name in our list endpoint).
        account = STATE_MACHINE_ARN.split(":")[4]
        region = STATE_MACHINE_ARN.split(":")[3]
        sm_name = STATE_MACHINE_ARN.split(":")[-1]
        execution_arn = (
            f"arn:aws:states:{region}:{account}:execution:{sm_name}:{job_id}"
        )

        try:
            desc = sfn.describe_execution(executionArn=execution_arn)
        except sfn.exceptions.ExecutionDoesNotExist:
            return _resp({"error": "not_found", "job_id": job_id}, 404)

        # Pull full history.
        events: list[dict[str, Any]] = []
        kw: dict[str, Any] = {"executionArn": execution_arn, "maxResults": 1000}
        while True:
            page = sfn.get_execution_history(**kw)
            events.extend(page.get("events", []))
            if "nextToken" in page:
                kw["nextToken"] = page["nextToken"]
            else:
                break

        timeline = _collapse_history(events)

        # Parse input → derive source PDF S3 key, union, period.
        try:
            inp = json.loads(desc.get("input", "{}"))
            s3_key = inp.get("detail", {}).get("object", {}).get("key", "")
        except Exception:
            s3_key = ""
        parts = s3_key.split("/") if s3_key else []
        union = period = ""
        if len(parts) >= 5 and parts[0] == "laboraid":
            union = f"{parts[1]} {parts[2]}"
            period = parts[3]

        # Output CSV is conventionally output.csv under the same prefix.
        output_csv_key = (
            "/".join(parts[:-1]) + "/output.csv" if parts else ""
        )

        # Check existence of artifacts before presigning so the UI can show
        # actual states (present / not yet produced) instead of dead links.
        artifacts: list[dict[str, Any]] = []
        if s3_key:
            artifacts.append({
                "name": "Source PDF",
                "kind": "input",
                "bucket": INPUTS_BUCKET,
                "key": s3_key,
                "size": None,
                "url": _presign(s3, INPUTS_BUCKET, s3_key),
            })
        if output_csv_key:
            size = None
            try:
                head = s3.head_object(Bucket=OUTPUTS_BUCKET, Key=output_csv_key)
                size = head.get("ContentLength")
            except Exception:
                pass
            artifacts.append({
                "name": "Output CSV",
                "kind": "output",
                "bucket": OUTPUTS_BUCKET,
                "key": output_csv_key,
                "size": size,
                "url": _presign(s3, OUTPUTS_BUCKET, output_csv_key)
                if size is not None
                else None,
            })

        start = desc.get("startDate")
        stop = desc.get("stopDate")
        duration_ms = (
            int((stop - start).total_seconds() * 1000) if (start and stop) else None
        )

        return _resp({
            "job_id": job_id,
            "execution_arn": execution_arn,
            "status": desc.get("status"),
            "started_at": start.isoformat() if start else None,
            "stopped_at": stop.isoformat() if stop else None,
            "duration_ms": duration_ms,
            "union": union,
            "period": period,
            "source_s3_key": s3_key,
            "output_csv_key": output_csv_key,
            "timeline": timeline,
            "artifacts": artifacts,
            "agent_log_group": (
                f"/aws/bedrock-agentcore/runtimes/{AGENT_RUNTIME_ID}-DEFAULT"
            ),
        })
    except Exception:
        logger.exception("job-status failed")
        raise
