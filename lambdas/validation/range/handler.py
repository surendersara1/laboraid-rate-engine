"""Range validator Lambda (Spec/09 §4 L6 §6.1).

Per-column sanity ranges that need no groundtruth: wages $5-200, fringe items
$0-30. Flags any cell outside its band.
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


WAGE_RANGE = (5.0, 200.0)
FRINGE_RANGE = (0.0, 30.0)
FRINGE_PREFIXES = ("health_welfare", "pension", "sis", "annuity", "industry")


def _band(canonical_field: str) -> tuple[float, float]:
    if canonical_field == "wage" or canonical_field.startswith("wage"):
        return WAGE_RANGE
    if canonical_field.startswith(FRINGE_PREFIXES):
        return FRINGE_RANGE
    return (0.0, 1_000.0)  # unknown column: loose guard


def validate(canonical: dict[str, Any]) -> dict[str, Any]:
    """Flag every out-of-range cell across all rows."""
    violations: list[dict[str, Any]] = []
    for row in canonical.get("rows", []):
        classification = row.get("classification", "?")
        for cell_key, cell in row.get("cells", {}).items():
            field = str(cell.get("canonical_field", cell_key))
            value = cell.get("value")
            if value is None:
                continue
            low, high = _band(field)
            if not (low <= float(value) <= high):
                violations.append(
                    {
                        "classification": classification,
                        "field": field,
                        "value": float(value),
                        "expected_range": [low, high],
                    }
                )
    return {"validator": "range", "passed": not violations, "violations": violations}


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Event carries the canonical rate sheet under ``canonical``."""
    try:
        result = validate(event["canonical"])
        logger.info("range validation complete")
        return result
    except Exception:
        logger.exception("range validator failed")
        raise
