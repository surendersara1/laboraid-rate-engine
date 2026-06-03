"""Pytest bootstrap for the Lambda handlers.

The API handlers import the shared ``authz`` module, which ships as a Lambda
layer (``/opt/python/authz.py``) in the deployed environment. For offline unit
tests we add the layer's source dir to ``sys.path`` so ``import authz`` resolves
the same module (audit B3).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parent / "api" / "_shared" / "python"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
