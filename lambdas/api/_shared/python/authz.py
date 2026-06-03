"""Per-route Cognito group authorization for the API Lambdas (Spec/09 §2.2, audit B3).

The HTTP-API Cognito JWT authorizer enforces *authentication* only; per-route
*group* gating (Admins / Operations / Business) is the Lambda's job. This module
is shipped as a Lambda layer (``/opt/python/authz.py``) and imported by every
gated handler.

Cognito surfaces the ``cognito:groups`` claim differently depending on the path:
through the HTTP API v2 JWT authorizer it arrives as a JSON-encoded / bracketed
list-string (e.g. ``"[Admins Operations]"`` or ``'["Admins"]'``), and via a
direct decode it may already be a list. ``extract_groups`` normalizes all of
these to a ``list[str]``.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FORBIDDEN_HEADERS = {"Content-Type": "application/json"}


def extract_groups(event: dict[str, Any]) -> list[str]:
    """Return the caller's Cognito groups as a list (empty when absent)."""
    raw = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
        .get("cognito:groups")
    )
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(g) for g in raw if str(g)]
    s = str(raw).strip()
    if not s:
        return []
    # JSON array string, e.g. '["Admins","Operations"]'.
    if s.startswith("["):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(g) for g in parsed if str(g)]
        except json.JSONDecodeError:
            pass
        # Bracketed space/comma list, e.g. "[Admins Operations]".
        s = s[1:-1] if s.endswith("]") else s[1:]
    return [g for g in re.split(r"[,\s]+", s.strip()) if g]


def enforce_groups(event: dict[str, Any], allowed: list[str]) -> dict[str, Any] | None:
    """Return a 403 response when the caller is in none of ``allowed``, else ``None``.

    Usage in a handler::

        denied = authz.enforce_groups(event, ALLOWED_GROUPS)
        if denied:
            return denied
    """
    groups = extract_groups(event)
    if any(g in allowed for g in groups):
        return None
    return {
        "statusCode": 403,
        "headers": dict(_FORBIDDEN_HEADERS),
        "body": json.dumps({"error": "forbidden", "required_groups": list(allowed)}),
    }
