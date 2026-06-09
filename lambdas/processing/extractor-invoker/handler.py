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
LLM_EXTRACTOR_FN = os.environ.get("LLM_EXTRACTOR_FN", "")

# Unions with a hand-coded kernel profile. Anything outside this set has no
# deterministic extractor and routes through the LLM extractor.
_KNOWN_KERNEL_UNIONS = {
    "pipe_fitters_537",
    "sprinkler_fitters_483",
    "sprinkler_fitters_704",
    "sprinkler_fitters_281",
    "sprinkler_fitters_821",
}


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


def _s3_prefix_from_key(s3_key: str) -> str:
    """Strip the filename off an S3 key to yield the union/period prefix."""
    if not s3_key or "/" not in s3_key:
        return ""
    return s3_key.rsplit("/", 1)[0] + "/"


def _direct_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Build a direct=True AgentCore payload from the SFN state.

    The SFN state at Stage 2 has Classify's output stored at $.classify; that's
    where the canonical union name + S3 key live.
    """
    classify = event.get("classify") or {}
    union = classify.get("union") or event.get("union") or ""
    s3_key = classify.get("s3_key") or event.get("s3_key") or ""
    return {
        "direct": True,
        "union": union,
        "s3_prefix": _s3_prefix_from_key(s3_key),
    }


def invoke_runtime(event: dict[str, Any]) -> dict[str, Any]:
    """Invoke the ExtractorAgent runtime in direct mode and return the parsed JSON
    result from the agent's entrypoint. Direct mode bypasses Strands/Claude so the
    fat tool runs in-process — deterministic for the 5 kernel unions, no chance
    for the Strands @tool JSON boundary to drop the kernel's RateCell typing.
    """
    payload = _direct_payload(event)
    resp = _client().invoke_agent_runtime(
        agentRuntimeArn=EXTRACTOR_RUNTIME_ARN,
        runtimeSessionId=_session_id(event),
        payload=json.dumps(payload).encode("utf-8"),
    )
    body = resp.get("response")
    raw = body.read() if hasattr(body, "read") else (body if isinstance(body, (bytes, bytearray)) else b"")
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except Exception as e:  # diagnostic path
        return {"_raw": raw[:1000].decode("utf-8", errors="replace"), "_parse_error": repr(e)}


def _invoke_llm_extractor(event: dict[str, Any]) -> dict[str, Any]:
    """Call the LLM extractor Lambda synchronously for unions without a
    kernel profile. Returns the same canonical shape AgentCore direct mode
    does so the downstream Publisher consumes both identically."""
    import boto3
    from botocore.config import Config

    lc = boto3.client(
        "lambda",
        config=Config(read_timeout=900, connect_timeout=10, retries={"max_attempts": 1}),
    )
    classify = event.get("classify") or {}
    out_s3_key = _default_llm_out_key(classify.get("s3_key") or "")
    payload = {"classify": classify, "out_s3_key": out_s3_key}
    resp = lc.invoke(
        FunctionName=LLM_EXTRACTOR_FN,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    body = resp["Payload"].read()
    if resp.get("FunctionError"):
        raise RuntimeError(
            f"llm-extractor returned FunctionError: {body[:500].decode('utf-8', errors='replace')}"
        )
    return json.loads(body.decode("utf-8")) if body else {}


def _default_llm_out_key(input_pdf_key: str) -> str:
    if "/" in input_pdf_key:
        prefix, _ = input_pdf_key.rsplit("/", 1)
        return f"{prefix}/output.csv"
    return "output.csv"


def _route(event: dict[str, Any]) -> dict[str, Any]:
    """Pick AgentCore (kernel) or LLM extractor based on the classifier's
    union name. Known kernel union -> deterministic AgentCore path; otherwise
    -> Bedrock Claude via the llm-extractor Lambda."""
    classify = event.get("classify") or {}
    union = (classify.get("union") or "").lower()
    if union in _KNOWN_KERNEL_UNIONS:
        logger.info("extractor-invoker: routing union=%s to AgentCore (kernel)", union)
        return invoke_runtime(event)
    if not LLM_EXTRACTOR_FN:
        raise RuntimeError(
            f"union={union!r} is not in the kernel set and LLM_EXTRACTOR_FN is not configured"
        )
    logger.info("extractor-invoker: routing union=%s to LLM extractor", union)
    return _invoke_llm_extractor(event)


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        result = _route(event)
        # Build a canonical-shaped key the downstream validators / review-router
        # read directly. Carries the agent's output csv key, gap list, checksum,
        # and the classify metadata (union/local/period/doc_type) under one
        # consistent shape — the audit had flagged that validators expected
        # event["canonical"] but SFN was passing raw extract output.
        classify = event.get("classify") or {}
        canonical = {
            "s3_key": result.get("s3_key"),
            "rows": result.get("rows", 0),
            "extracted_rows": result.get("extracted_rows", 0),
            "gaps": result.get("gaps", []),
            "gap_count": result.get("gap_count", 0),
            "checksum": result.get("checksum"),
            "union": classify.get("union"),
            "local": classify.get("local"),
            "period": classify.get("period"),
            "doc_type": classify.get("doc_type"),
            "source_s3_key": classify.get("s3_key"),
        }
        return {"extracted": True, "canonical": canonical, "runtime_response": result}
    except Exception:
        logger.exception("extractor-invoker failed")
        raise
