"""Authz test for the job-retry handler (audit B3: Admins/Operations only)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "job_retry_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_authz_forbidden_when_group_empty() -> None:
    """A JWT with an empty cognito:groups claim must get 403."""
    event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"cognito:groups": "[]"}}}},
        "body": "{}",
    }
    result = _mod.handler(event, None)
    assert result["statusCode"] == 403


def test_authz_forbidden_when_group_missing() -> None:
    """A JWT with no cognito:groups claim must get 403."""
    event = {"requestContext": {"authorizer": {"jwt": {"claims": {}}}}, "body": "{}"}
    result = _mod.handler(event, None)
    assert result["statusCode"] == 403
