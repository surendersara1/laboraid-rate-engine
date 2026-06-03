"""xlsx renderer Lambda (Spec/09 §4 L7 §7.2).

The kernel's `pivot.py` writes a CSV matching each union's groundtruth header.
537's groundtruth is XLSX, so this Lambda reads the kernel CSV from S3 and writes
the same data as XLSX (same column order) to the outputs bucket.

Pure CSV parsing is unit-testable; openpyxl + S3 are used only in `handler`.
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


def parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    """Parse ratesheet CSV text into (header, rows)."""
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    return rows[0], rows[1:]


def build_xlsx_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    """Render header+rows to an XLSX workbook (same column order)."""
    import openpyxl  # imported here: heavy native dep, not needed for unit tests

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Rate Sheet"
    ws.append(header)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Event: ``{csv_s3_key, out_s3_key}``. Reads kernel CSV, writes XLSX."""
    try:
        import boto3

        s3 = boto3.client("s3")
        inputs_bucket = os.environ["OUTPUTS_BUCKET"]
        csv_text = s3.get_object(Bucket=inputs_bucket, Key=event["csv_s3_key"])["Body"].read()
        header, rows = parse_csv(csv_text.decode("utf-8"))
        xlsx = build_xlsx_bytes(header, rows)
        s3.put_object(
            Bucket=inputs_bucket,
            Key=event["out_s3_key"],
            Body=xlsx,
            ServerSideEncryption="aws:kms",
        )
        logger.info("rendered xlsx with %d data rows", len(rows))
        return {"s3_key": event["out_s3_key"], "rows": len(rows)}
    except Exception:
        logger.exception("xlsx renderer failed")
        raise
