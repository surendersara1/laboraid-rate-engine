"""Tests for the ExtractorInvoker (mocks bedrock-agentcore:InvokeAgentRuntime)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_spec = importlib.util.spec_from_file_location(
    "extractor_invoker_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

handler = _mod.handler


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"statusCode": 200}


def test_handler_invokes_runtime(monkeypatch: Any) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(_mod, "_client", lambda: fake)
    monkeypatch.setattr(_mod, "EXTRACTOR_RUNTIME_ARN", "arn:aws:bedrock-agentcore:::runtime/x")
    event = {"job_id": "job-123", "union": "150", "s3_prefix": "inbox/150/"}
    result = handler(event, None)
    assert result["extracted"] is True
    assert result["runtime_response"]["statusCode"] == 200
    assert len(fake.calls) == 1
    assert fake.calls[0]["agentRuntimeArn"] == "arn:aws:bedrock-agentcore:::runtime/x"
    # AgentCore requires a session id of at least 33 chars.
    assert len(fake.calls[0]["runtimeSessionId"]) >= 33


def test_handler_propagates_runtime_error(monkeypatch: Any) -> None:
    class _Boom:
        def invoke_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("runtime unavailable")

    monkeypatch.setattr(_mod, "_client", lambda: _Boom())
    try:
        handler({"job_id": "job-9"}, None)
    except RuntimeError as exc:
        assert "runtime unavailable" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError to propagate")
