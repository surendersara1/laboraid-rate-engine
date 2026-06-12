"""Profile list / get Lambda (Spec/09 §4 L2).

Two routes wired to the same handler, now backed by AURORA (unions table) —
the system of record. Profiles are built by the profile-builder (CBA -> Aurora)
or auto-onboarded on first upload; this surfaces them to the Admin Profiles tab.

  GET /v1/unions                  -> list of unions with trade/local/parent
  GET /v1/unions/{local}/profile  -> single union's metadata + profile JSON
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


def _rows(sql: str, params: list[dict[str, Any]] | None = None) -> list[list[dict[str, Any]]]:
    import boto3

    rds = boto3.client("rds-data")
    return rds.execute_statement(
        resourceArn=os.environ["AURORA_CLUSTER_ARN"],
        secretArn=os.environ["AURORA_SECRET_ARN"],
        database="laboraid",
        sql=sql,
        parameters=params or [],
    ).get("records", [])


def _slug(trade: str, local: Any) -> str:
    return f"{(trade or '').lower().replace(' ', '_')}_{local}"


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        params = event.get("pathParameters") or {}
        local = params.get("local")

        # /v1/unions/{local}/profile → single profile detail
        if local:
            recs = _rows(
                "SELECT local, trade, parent_intl, profile_yaml::text, profile_version "
                "FROM unions WHERE local = :l::int",
                [{"name": "l", "value": {"stringValue": str(local)}}],
            )
            if not recs:
                return _resp({"error": "not_found", "local": local}, 404)
            r = recs[0]
            trade = r[1].get("stringValue") or ""
            raw = None if r[3].get("isNull") else r[3].get("stringValue")
            # Pretty-print the stored JSON profile for the editor.
            profile_yaml = json.dumps(json.loads(raw), indent=2) if raw else None
            return _resp({
                "slug": _slug(trade, r[0].get("longValue")),
                "trade": trade,
                "local": r[0].get("longValue"),
                "parent": r[2].get("stringValue") or "UA",
                "profile_version": r[4].get("stringValue"),
                "profile_yaml": profile_yaml,
                "has_yaml": profile_yaml is not None,
            })

        # /v1/unions → list with metadata
        recs = _rows(
            "SELECT local, trade, parent_intl, (profile_yaml IS NOT NULL), profile_version "
            "FROM unions ORDER BY local"
        )
        unions = [{
            "slug": _slug(r[1].get("stringValue"), r[0].get("longValue")),
            "trade": r[1].get("stringValue"),
            "local": r[0].get("longValue"),
            "parent": r[2].get("stringValue") or "UA",
            "has_profile": bool(r[3].get("booleanValue")),
            "profile_version": r[4].get("stringValue"),
        } for r in recs]
        return _resp({"unions": unions, "count": len(unions)})
    except Exception:
        logger.exception("profile-list failed")
        raise
