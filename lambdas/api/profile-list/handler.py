"""Profile list / get Lambda (Spec/09 §4 L2).

Two routes wired to the same handler:
  GET /v1/unions                  -> list of unions with trade/local/parent
  GET /v1/unions/{local}/profile  -> single union's metadata + profile YAML
"""

from __future__ import annotations

import json
import os
from typing import Any

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
        "body": json.dumps(body),
    }


UNION_META = {
    "pipe_fitters_537": {"trade": "Pipefitter", "local": 537, "parent": "UA"},
    "sprinkler_fitters_483": {"trade": "Sprinkler", "local": 483, "parent": "UA"},
    "sprinkler_fitters_704": {"trade": "Sprinkler", "local": 704, "parent": "UA"},
    "sprinkler_fitters_281": {"trade": "Sprinkler", "local": 281, "parent": "UA"},
    "sprinkler_fitters_821": {"trade": "Sprinkler", "local": 821, "parent": "UA"},
}


def _load_profile_yaml(slug: str) -> str | None:
    for p in (f"/var/task/profiles/{slug}.yaml", f"/opt/profiles/{slug}.yaml"):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
    return None


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        params = event.get("pathParameters") or {}
        local = params.get("local")

        # /v1/unions/{local}/profile → single profile detail
        if local:
            slug = next(
                (u for u, m in UNION_META.items() if str(m["local"]) == str(local) or u == local),
                None,
            )
            if slug is None:
                return _resp({"error": "not_found", "local": local}, 404)
            yaml_text = _load_profile_yaml(slug)
            meta = UNION_META[slug]
            return _resp({
                "slug": slug,
                "trade": meta["trade"],
                "local": meta["local"],
                "parent": meta["parent"],
                "profile_yaml": yaml_text,
                "has_yaml": yaml_text is not None,
            })

        # /v1/unions → list with metadata
        unions = [
            {
                "slug": u,
                "trade": UNION_META[u]["trade"],
                "local": UNION_META[u]["local"],
                "parent": UNION_META[u]["parent"],
            }
            for u in UNION_META
        ]
        return _resp({"unions": unions, "count": len(unions)})
    except Exception:
        logger.exception("profile-list failed")
        raise
