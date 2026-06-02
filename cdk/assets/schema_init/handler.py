"""Schema-init custom-resource handler (Spec/09 §3.3).

CloudFormation custom resource (via CDK ``Provider``) that applies the Aurora
DDL in ``schema.sql`` using the RDS Data API — no VPC attachment or psycopg
needed. Idempotent: the DDL uses ``IF NOT EXISTS`` so re-runs are safe.

Env vars:
    CLUSTER_ARN  Aurora cluster ARN
    SECRET_ARN   Secrets Manager secret ARN (DB master credentials)
    DB_NAME      Target database name
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import boto3

_rds_data = boto3.client("rds-data")


def _statements() -> list[str]:
    sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    # Strip line comments, then split on ';'. schema.sql avoids ';' in literals.
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    body = "\n".join(lines)
    return [s.strip() for s in body.split(";") if s.strip()]


def _apply() -> int:
    cluster_arn = os.environ["CLUSTER_ARN"]
    secret_arn = os.environ["SECRET_ARN"]
    db_name = os.environ["DB_NAME"]
    count = 0
    for stmt in _statements():
        _rds_data.execute_statement(
            resourceArn=cluster_arn,
            secretArn=secret_arn,
            database=db_name,
            sql=stmt,
        )
        count += 1
    return count


def on_event(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Custom-resource lifecycle entry point."""
    request_type = event.get("RequestType")
    if request_type in ("Create", "Update"):
        applied = _apply()
        return {"PhysicalResourceId": "laboraid-schema-init", "Data": {"Applied": applied}}
    # Delete: leave the schema in place (data retention); nothing to undo.
    return {"PhysicalResourceId": event.get("PhysicalResourceId", "laboraid-schema-init")}
