"""CSV renderer Lambda (Spec/09 §4 L7 §7.1).

The kernel already produces a groundtruth-matching CSV via `pivot.write_csv`; for
704/821/483/281 that is the final artifact. This Lambda relocates the kernel CSV
to the canonical outputs key and validates the header is well-formed.
"""

from __future__ import annotations

import csv
import io
import os
from typing import Any

try:  # pragma: no cover - present in the Lambda runtime
    from aws_lambda_powertools import Logger, Tracer

    logger = Logger(service="laboraid-rendering")
    tracer = Tracer()

    def _instrument(fn: Any) -> Any:
        return logger.inject_lambda_context(tracer.capture_lambda_handler(fn))

except ModuleNotFoundError:  # pragma: no cover - offline unit-test env
    import logging

    logger = logging.getLogger("laboraid-rendering")  # type: ignore[assignment]

    def _instrument(fn: Any) -> Any:
        return fn


REQUIRED_COLUMNS = ("Union Group", "Trade", "Union Local", "Zone", "Package")


def validate_header(text: str) -> dict[str, Any]:
    """Confirm the CSV header contains the required key columns."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, [])
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    return {"valid": not missing, "header": header, "missing": missing}


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Event: ``{csv_s3_key, out_s3_key}``. Validates + relocates the kernel CSV."""
    try:
        import boto3

        s3 = boto3.client("s3")
        bucket = os.environ["OUTPUTS_BUCKET"]
        body = s3.get_object(Bucket=bucket, Key=event["csv_s3_key"])["Body"].read()
        check = validate_header(body.decode("utf-8"))
        if not check["valid"]:
            raise ValueError(f"CSV missing required columns: {check['missing']}")
        s3.put_object(
            Bucket=bucket,
            Key=event["out_s3_key"],
            Body=body,
            ServerSideEncryption="aws:kms",
        )
        logger.info("relocated CSV to %s", event["out_s3_key"])
        return {"s3_key": event["out_s3_key"], "columns": len(check["header"])}
    except Exception:
        logger.exception("csv renderer failed")
        raise
