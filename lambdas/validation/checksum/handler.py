"""Checksum validator Lambda (Spec/09 §4 L6 §6.1).

Pre-publish gate that runs WITHOUT groundtruth: for each classification row,
verify wage + fringes equals the printed Total Package (±$0.05). Operates on the
canonical JSON the engine produced (rows -> cells -> value/canonical_field).

Powertools is imported optionally so the pure logic is unit-testable offline.
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


TOLERANCE = 0.05
FRINGE_PREFIXES = ("health_welfare", "pension", "sis", "annuity", "industry")


def check_row(row: dict[str, Any]) -> dict[str, Any]:
    """Validate one classification row's Total Package checksum."""
    cells = row.get("cells", {})
    wage = float(cells.get("wage", {}).get("value", 0.0))
    fringes = sum(
        float(c["value"])
        for c in cells.values()
        if str(c.get("canonical_field", "")).startswith(FRINGE_PREFIXES)
    )
    computed = round(wage + fringes, 2)
    expected = row.get("notice_total")
    classification = row.get("classification", "?")
    if expected is None:
        return {
            "classification": classification,
            "passed": None,
            "reason": "notice did not print a Total Package",
            "computed": computed,
        }
    diff = round(computed - float(expected), 2)
    return {
        "classification": classification,
        "passed": abs(diff) <= TOLERANCE,
        "computed": computed,
        "expected": float(expected),
        "diff": diff,
    }


def validate(canonical: dict[str, Any]) -> dict[str, Any]:
    """Validate all rows; overall pass requires every checkable row to pass."""
    results = [check_row(r) for r in canonical.get("rows", [])]
    checkable = [r for r in results if r["passed"] is not None]
    passed = all(r["passed"] for r in checkable)
    return {
        "validator": "checksum",
        "passed": passed,
        "rows": results,
        "failures": [r for r in checkable if not r["passed"]],
    }


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Event carries the canonical rate sheet under ``canonical``."""
    try:
        result = validate(event["canonical"])
        logger.info("checksum validation complete")
        return result
    except Exception:
        logger.exception("checksum validator failed")
        raise
