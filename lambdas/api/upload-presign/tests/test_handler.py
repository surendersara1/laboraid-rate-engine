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
