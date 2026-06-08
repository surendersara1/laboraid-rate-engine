"""Rate sheet list Lambda (Spec/09 §4 L2). Lists rate periods by approval_state. Cognito/M2M."""

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


def _sub(event: dict[str, Any]) -> str:
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
        .get("sub", "unknown")
    )


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        path_params = event.get("pathParameters") or {}
        params = event.get("queryStringParameters") or {}
        state = params.get("approval_state")
        local = path_params.get("local")
        # UI uses /v1/unions/all/rate-sheets when listing across all unions.
        filter_union = local not in (None, "all", "")

        sql = (
            "SELECT rp.id::text, u.local, u.trade, "
            "       to_char(rp.start_date,'YYYY-MM-DD') AS period, "
            "       rp.approval_state, "
            "       COALESCE((rp.canonical_json->>'gaps')::int, 0) AS gap_count "
            "  FROM rate_periods rp JOIN unions u ON u.id = rp.union_id"
        )
        clauses: list[str] = []
        params_list: list[dict[str, Any]] = []
        if state:
            clauses.append("rp.approval_state = :state")
            params_list.append({"name": "state", "value": {"stringValue": state}})
        if filter_union:
            clauses.append("u.local = :local::int")
            params_list.append({"name": "local", "value": {"stringValue": str(local)}})
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY rp.start_date DESC LIMIT 200"

        import boto3

        kwargs: dict[str, Any] = dict(
            resourceArn=os.environ["AURORA_CLUSTER_ARN"],
            secretArn=os.environ["AURORA_SECRET_ARN"],
            database="laboraid",
            sql=sql,
        )
        if params_list:
            kwargs["parameters"] = params_list
        resp = boto3.client("rds-data").execute_statement(**kwargs)

        # Re-shape raw rds-data records into the {union, period, approval_state,
        # gap_count} contract the SPA's RateSheetSummary type expects.
        records: list[dict[str, Any]] = []
        for row in resp.get("records", []):
            local_int = row[1].get("longValue")
            trade = row[2].get("stringValue") or ""
            records.append({
                "id": row[0].get("stringValue"),
                "union": f"{trade} {local_int}".strip() if local_int else trade,
                "local": local_int,
                "trade": trade,
                "period": row[3].get("stringValue"),
                "approval_state": row[4].get("stringValue"),
                "gap_count": row[5].get("longValue", 0),
            })
        return _resp({"records": records})
    except Exception:
        logger.exception("ratesheet-list failed")
        raise
