"""Tests for upload-presign key building."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "upload_presign_handler", Path(__file__).resolve().parent.parent / "handler.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

build_key = _mod.build_key


def test_build_key_strips_path() -> None:
    assert build_key("2026.01.01.704 Rate Notice.pdf").endswith("704 Rate Notice.pdf")
    assert build_key("../../etc/passwd") == "laboraid/uploads/passwd"


def test_authz_forbidden_when_group_empty() -> None:
    """audit B3: a JWT with an empty cognito:groups claim must get 403."""
    event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"cognito:groups": "[]"}}}},
        "body": "{}",
    }
    result = _mod.handler(event, None)
    assert result["statusCode"] == 403


def test_authz_forbidden_when_group_missing() -> None:
    """audit B3: a JWT with no cognito:groups claim must get 403."""
    event = {"requestContext": {"authorizer": {"jwt": {"claims": {}}}}, "body": "{}"}
    result = _mod.handler(event, None)
    assert result["statusCode"] == 403
