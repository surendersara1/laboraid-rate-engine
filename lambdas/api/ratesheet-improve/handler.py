"""Rate sheet improve Lambda (Phase 2). Business clicks "Improve" -> this records
an improvement_run over the sheet's OPEN corrections, dispatches asynchronously,
and returns the run id immediately. The async leg invokes the ImproverAgent on
AgentCore (which writes v+1 and updates the run). Business persona.

Self-async (mirrors ratesheet-rework): the sync API call re-invokes this same
Lambda with `_async: true` (InvocationType=Event), so the API returns fast while
the agent runs for as long as it needs.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import authz  # shared Lambda layer (/opt/python/authz.py)

try:  # pragma: no cover - present in the Lambda runtime
    from aws_lambda_powertools import Logger, Tracer

    logger = Logger(service="laboraid-api")
    tracer = Tracer()

    def _instrument(fn: Any) -> Any:
        return logger.inject_lambda_context(tracer.capture_lambda_handler(fn))

except ModuleNotFoundError:  # pragma: no cover - offline
    import logging

    logger = logging.getLogger("laboraid-api")  # type: ignore[assignment]

    def _instrument(fn: Any) -> Any:
        return fn


def _resp(body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    return {"statusCode": status, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body)}


def _actor(event: dict[str, Any]) -> str:
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    return claims.get("email") or claims.get("cognito:username") or claims.get("sub") or "unknown"


ALLOWED_GROUPS = ["Business"]
IMPROVER_RUNTIME_ARN = os.environ.get("IMPROVER_RUNTIME_ARN", "")
MODEL_ID = "us.anthropic.claude-opus-4-5-20251101-v1:0"
DB = {
    "resourceArn": os.environ.get("AURORA_CLUSTER_ARN", ""),
    "secretArn": os.environ.get("AURORA_SECRET_ARN", ""),
    "database": "laboraid",
}


def _run_agent(local: str, period: str, run_id: str) -> dict[str, Any]:
    """Async leg: invoke the ImproverAgent runtime. The agent updates the run; we
    only mark it failed if the invocation itself errors."""
    import boto3

    try:
        boto3.client("bedrock-agentcore").invoke_agent_runtime(
            agentRuntimeArn=IMPROVER_RUNTIME_ARN,
            runtimeSessionId=run_id,  # uuid is 36 chars (>= 33 required)
            payload=json.dumps({"local": local, "period": period, "run_id": run_id}).encode(),
        )
        return {"ok": True, "run_id": run_id}
    except Exception as e:
        logger.exception("improve: agent invocation failed")
        try:
            boto3.client("rds-data").execute_statement(
                **DB,
                sql="UPDATE improvement_runs SET status='failed', finished_at=NOW(), error=:e WHERE id=:id::uuid",
                parameters=[{"name": "e", "value": {"stringValue": str(e)[:1000]}},
                            {"name": "id", "value": {"stringValue": run_id}}],
            )
        except Exception:
            pass
        raise


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        if event.get("_async") is True:
            return _run_agent(event["local"], event["period"], event["run_id"])

        denied = authz.enforce_groups(event, ALLOWED_GROUPS)
        if denied:
            return denied
        p = event["pathParameters"]
        local, period = p["local"], p["period"]
        import boto3

        rds = boto3.client("rds-data")
        rows = rds.execute_statement(
            **DB,
            sql="SELECT rp.id::text, rp.version FROM rate_periods rp JOIN unions u ON u.id=rp.union_id "
                "WHERE u.local=:l::int AND to_char(rp.start_date,'YYYY-MM-DD')=:p ORDER BY rp.version DESC LIMIT 1",
            parameters=[{"name": "l", "value": {"stringValue": local}},
                        {"name": "p", "value": {"stringValue": period}}],
        ).get("records", [])
        if not rows:
            return _resp({"error": "not_found", "local": local, "period": period}, 404)
        period_id = rows[0][0]["stringValue"]
        version = rows[0][1].get("longValue", 1)

        cnt = rds.execute_statement(
            **DB,
            sql="SELECT count(*) FROM cell_corrections WHERE union_local=:l AND period=:p AND status='open'",
            parameters=[{"name": "l", "value": {"stringValue": local}},
                        {"name": "p", "value": {"stringValue": period}}],
        )["records"][0][0]["longValue"]
        if cnt == 0:
            return _resp({"error": "no_open_corrections",
                          "message": "Nothing to improve — no open comments or overrides."}, 422)

        run_id = str(uuid.uuid4())
        rds.execute_statement(
            **DB,
            sql="INSERT INTO improvement_runs (id, period_id, union_local, period, from_version, "
                "triggered_by, model, status) "
                "VALUES (:id::uuid, :pid::uuid, :l, :p, :fv, :by, :model, 'running')",
            parameters=[
                {"name": "id", "value": {"stringValue": run_id}},
                {"name": "pid", "value": {"stringValue": period_id}},
                {"name": "l", "value": {"stringValue": local}},
                {"name": "p", "value": {"stringValue": period}},
                {"name": "fv", "value": {"longValue": int(version)}},
                {"name": "by", "value": {"stringValue": _actor(event)}},
                {"name": "model", "value": {"stringValue": MODEL_ID}},
            ],
        )
        boto3.client("lambda").invoke(
            FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
            InvocationType="Event",
            Payload=json.dumps({"_async": True, "local": local, "period": period, "run_id": run_id}).encode(),
        )
        return _resp({"run_id": run_id, "status": "running", "corrections": cnt,
                      "message": f"Improving {cnt} correction(s) — a new version will appear shortly."})
    except Exception:
        logger.exception("ratesheet-improve failed")
        raise
