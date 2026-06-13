"""Job detail Lambda — reads the jobs DynamoDB read-model (Admins/Operations).

WAS: live DescribeExecution + GetExecutionHistory (paginated) on every open.
NOW: one get_item on the `jobs` table (populated by the job-writer), plus S3
presigns for the artifacts. No Step Functions calls. The pipeline `trace` and a
3-stage timeline are stored on the item, so the Job Detail page renders the full
"calls in the pipeline" view straight from DynamoDB.
"""

from __future__ import annotations

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


def _resp(body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


ALLOWED_GROUPS = ["Admins", "Operations"]
INPUTS_BUCKET = os.environ.get("INPUTS_BUCKET", "laboraid-dev-l3-bucket-inputs")
OUTPUTS_BUCKET = os.environ.get("OUTPUTS_BUCKET", "laboraid-dev-l3-bucket-outputs")
SYNTH_LOG_GROUP = "/aws/lambda/laboraid-dev-l4-fn-synthesizer"


def _num(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v % 1 == 0 else float(v)
    return v


def _presign(s3: Any, bucket: str, key: str) -> str | None:
    if not key:
        return None
    try:
        return s3.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=43200
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

        s3 = boto3.client("s3")
        table = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])
        it = table.get_item(Key={"job_id": job_id}).get("Item")
        if not it:
            return _resp({"error": "not_found", "job_id": job_id}, 404)

        # Artifacts — presigned at read time (S3 only, no SFN).
        artifacts: list[dict[str, Any]] = []
        keys = it.get("source_keys") or []
        for sk in keys:
            artifacts.append({
                "name": sk.rsplit("/", 1)[-1] if len(keys) > 1 else "Source PDF",
                "kind": "input",
                "bucket": INPUTS_BUCKET,
                "key": sk,
                "url": _presign(s3, INPUTS_BUCKET, sk),
            })
        for label, key in (("Output CSV", it.get("output_csv")), ("Output XLSX", it.get("output_xlsx"))):
            if not key:
                continue
            size = None
            try:
                size = s3.head_object(Bucket=OUTPUTS_BUCKET, Key=key).get("ContentLength")
            except Exception:
                pass
            artifacts.append({
                "name": label,
                "kind": "output",
                "bucket": OUTPUTS_BUCKET,
                "key": key,
                "size": _num(size),
                "url": _presign(s3, OUTPUTS_BUCKET, key) if size is not None else None,
            })

        return _resp({
            "job_id": it.get("job_id"),
            "execution_arn": it.get("execution_arn"),
            "status": it.get("status"),
            "started_at": it.get("started_at"),
            "stopped_at": it.get("stopped_at"),
            "duration_ms": _num(it.get("duration_ms")),
            "union": it.get("union"),
            "local": it.get("local"),
            "period": it.get("period"),
            "row_count": _num(it.get("row_count")),
            "cell_count": _num(it.get("cell_count")),
            "error": it.get("error"),
            "timeline": it.get("timeline") or [],
            "trace": it.get("trace") or [],
            "artifacts": artifacts,
            "agent_log_group": SYNTH_LOG_GROUP,
        })
    except Exception:
        logger.exception("job-status failed")
        raise
