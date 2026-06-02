"""Articles renderer Lambda (Spec/09 §4 L7 §7.1).

The kernel writes a per-union ``<union>.gaps.md`` itemizing blank/divergent cells
(the never-fabricate trail). This Lambda parses that markdown table into a
structured "Articles" CSV the Business persona can review alongside the rate
sheet. Pure markdown parsing is unit-testable; S3 is used only in `handler`.
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


def parse_gaps_md(text: str) -> list[dict[str, str]]:
    """Parse the kernel gaps.md markdown table into structured rows.

    Expected header: ``| Zone | Package | Column | Reason |``. The separator row
    (dashes) and any non-table prose are ignored.
    """
    entries: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 4:
            continue
        if cells == ["Zone", "Package", "Column", "Reason"]:
            continue  # header row
        if all(set(c) <= {"-", ":"} and c for c in cells):
            continue  # separator row
        entries.append(
            {"zone": cells[0], "package": cells[1], "column": cells[2], "reason": cells[3]}
        )
    return entries


def to_articles_csv(entries: list[dict[str, str]]) -> str:
    """Render parsed gap entries to CSV text."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["zone", "package", "column", "reason"])
    writer.writeheader()
    writer.writerows(entries)
    return buf.getvalue()


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Event: ``{gaps_s3_key, out_s3_key}``. Parses gaps.md, writes Articles CSV."""
    try:
        import boto3

        s3 = boto3.client("s3")
        bucket = os.environ["OUTPUTS_BUCKET"]
        md = s3.get_object(Bucket=bucket, Key=event["gaps_s3_key"])["Body"].read()
        entries = parse_gaps_md(md.decode("utf-8"))
        s3.put_object(
            Bucket=bucket,
            Key=event["out_s3_key"],
            Body=to_articles_csv(entries).encode("utf-8"),
            ServerSideEncryption="aws:kms",
        )
        logger.info("rendered %d article entries", len(entries))
        return {"s3_key": event["out_s3_key"], "entries": len(entries)}
    except Exception:
        logger.exception("articles renderer failed")
        raise
