"""ExtractorInvoker Lambda (Spec/09 §4 L3, audit B6).

Step Functions Stage 2 invokes this Lambda to run the ExtractorAgent on AgentCore
Runtime synchronously: there is no native SFN -> AgentCore service integration, so
this thin invoker calls ``bedrock-agentcore:InvokeAgentRuntime`` with the
classified document + run context the upstream Classify stage produced, and
returns the agent's response to the state machine.
"""

from __future__ import annotations

import json
import os
from typing import Any

try:  # pragma: no cover - present in the Lambda runtime
    from aws_lambda_powertools import Logger, Tracer

    logger = Logger(service="laboraid-extractor-invoker")
    tracer = Tracer()

    def _instrument(fn: Any) -> Any:
        return logger.inject_lambda_context(tracer.capture_lambda_handler(fn))

except ModuleNotFoundError:  # pragma: no cover - offline unit-test env
    import logging

    logger = logging.getLogger("laboraid-extractor-invoker")  # type: ignore[assignment]

    def _instrument(fn: Any) -> Any:
        return fn


EXTRACTOR_RUNTIME_ARN = os.environ.get("EXTRACTOR_RUNTIME_ARN", "")


def _client() -> Any:
    import boto3
    from botocore.config import Config

    # AgentCore Runtime invocations run synchronously and can take 5-15 minutes
    # (kernel pipeline + Bedrock Converse calls). Default boto3 read timeout is
    # 60s — far too short — so the SDK would raise ReadTimeout while the agent
    # is still working. Match AgentCore's max session of 15 min and disable retries
    # so a long-running invoke doesn't trigger a duplicate side-by-side invocation.
    return boto3.client(
        "bedrock-agentcore",
        config=Config(read_timeout=900, connect_timeout=10, retries={"max_attempts": 1}),
    )


def _session_id(event: dict[str, Any]) -> str:
    """AgentCore requires a runtime session id of >= 33 chars; pad the job id."""
    base = str(event.get("job_id") or event.get("jobId") or "extractor-session")
    return (base + "-" + "0" * 33)[:64] if len(base) < 33 else base


def invoke_runtime(event: dict[str, Any]) -> dict[str, Any]:
    """Invoke the ExtractorAgent runtime synchronously, returning a result summary."""
    resp = _client().invoke_agent_runtime(
        agentRuntimeArn=EXTRACTOR_RUNTIME_ARN,
        runtimeSessionId=_session_id(event),
        payload=json.dumps(event).encode("utf-8"),
    )
    return {"statusCode": resp.get("statusCode", 200)}


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        result = invoke_runtime(event)
        return {"extracted": True, "runtime_response": result}
    except Exception:
        logger.exception("extractor-invoker failed")
        raise
