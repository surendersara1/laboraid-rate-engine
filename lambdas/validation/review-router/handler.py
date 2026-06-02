"""Review-router Lambda (Spec/09 §4 L6 §6.1).

Takes the low-confidence cells surfaced by the confidence validator and writes
them to the `review` DynamoDB queue so the Business persona's review page can
work them. Pure item-building is unit-testable; the DynamoDB write is lazy.
"""

from __future__ import annotations

import os
from typing import Any

try:  # pragma: no cover - present in the Lambda runtime
    from aws_lambda_powertools import Logger, Tracer

    logger = Logger(service="laboraid-validation")
    tracer = Tracer()

    def _instrument(fn: Any) -> Any:
        return logger.inject_lambda_context(tracer.capture_lambda_handler(fn))

except ModuleNotFoundError:  # pragma: no cover - offline unit-test env
    import logging

    logger = logging.getLogger("laboraid-validation")  # type: ignore[assignment]

    def _instrument(fn: Any) -> Any:
        return fn


def build_review_items(
    *,
    tenant: str,
    union: str,
    period: str,
    created_at: str,
    low_confidence_cells: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build `review` table items (pk=tenant, sk=created_at#cell_id)."""
    items: list[dict[str, Any]] = []
    for idx, cell in enumerate(low_confidence_cells):
        cell_id = f"{union}#{period}#{cell.get('classification', '?')}#{cell.get('field', idx)}"
        items.append(
            {
                "tenant": tenant,
                "created_at#cell_id": f"{created_at}#{cell_id}",
                "union": union,
                "period": period,
                "field": cell.get("field"),
                "confidence": cell.get("confidence"),
                "status": "pending",
            }
        )
    return items


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Event: ``{tenant, union, period, created_at, low_confidence_cells}``."""
    try:
        items = build_review_items(
            tenant=event.get("tenant", "laboraid"),
            union=event["union"],
            period=event["period"],
            created_at=event["created_at"],
            low_confidence_cells=event.get("low_confidence_cells", []),
        )
        table_name = os.environ.get("REVIEW_TABLE")
        if items and table_name:
            import boto3  # lazy: not needed for unit tests

            table = boto3.resource("dynamodb").Table(table_name)
            with table.batch_writer() as batch:
                for item in items:
                    batch.put_item(Item=item)
        logger.info("routed %d cells to review", len(items))
        return {"routed": len(items)}
    except Exception:
        logger.exception("review router failed")
        raise
