"""Confidence rollup validator Lambda (Spec/09 §4 L6 §6.1).

Aggregates per-cell confidence and routes any cell below the threshold to the
review queue. Overall pass = no low-confidence cells.
"""

from __future__ import annotations

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


THRESHOLD = 0.85


def validate(canonical: dict[str, Any], threshold: float = THRESHOLD) -> dict[str, Any]:
    """Roll up confidence; list cells below ``threshold``."""
    confidences: list[float] = []
    low_confidence: list[dict[str, Any]] = []
    for row in canonical.get("rows", []):
        classification = row.get("classification", "?")
        for cell_key, cell in row.get("cells", {}).items():
            conf = cell.get("confidence")
            if conf is None:
                continue
            conf = float(conf)
            confidences.append(conf)
            if conf < threshold:
                low_confidence.append(
                    {
                        "classification": classification,
                        "field": str(cell.get("canonical_field", cell_key)),
                        "confidence": conf,
                    }
                )
    mean = round(sum(confidences) / len(confidences), 4) if confidences else None
    return {
        "validator": "confidence",
        "passed": not low_confidence,
        "mean_confidence": mean,
        "min_confidence": min(confidences) if confidences else None,
        "low_confidence_cells": low_confidence,
    }


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Event carries the canonical rate sheet under ``canonical``."""
    try:
        result = validate(event["canonical"])
        logger.info("confidence rollup complete")
        return result
    except Exception:
        logger.exception("confidence validator failed")
        raise
