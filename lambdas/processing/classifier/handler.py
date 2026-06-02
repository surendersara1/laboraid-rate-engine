"""Document classifier Lambda (Spec/09 §4 L4 §4.2).

Stage 1 of the pipeline: given an uploaded S3 key, identify document type, union,
period, and format. Deterministic filename-regex first; an ambiguous result falls
back to a Bedrock Claude Haiku call. The kernel does not classify — it assumes the
union is named upfront — so this is net-new (kernel rule #7).

Filenames look like ``2026.01.01.704 Rate Notice.pdf`` →
``{period}.{local} {doc_type}.pdf``.
"""

from __future__ import annotations

import os
import re
from typing import Any

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger()
tracer = Tracer()
metrics = Metrics()

# 2026.01.01.704 Rate Notice.pdf  ->  date=2026.01.01 local=704 doc=Rate Notice
_FILENAME = re.compile(
    r"(?P<date>\d{4}\.\d{2}\.\d{2})\.(?P<local>\d{3})\s+(?P<doc>.+?)\.pdf$",
    re.IGNORECASE,
)

_DOC_TYPES = {
    "rate notice": "rate_notice",
    "rate sheet": "rate_sheet",
    "cba": "cba",
    "agreement": "cba",
}

# local -> kernel union key (the kernel's EXTRACTORS keys).
_LOCAL_TO_UNION = {
    "537": "pipe_fitters_537",
    "483": "sprinkler_fitters_483",
    "704": "sprinkler_fitters_704",
    "281": "sprinkler_fitters_281",
    "821": "sprinkler_fitters_821",
}


def _classify_by_filename(key: str) -> dict[str, Any] | None:
    match = _FILENAME.search(os.path.basename(key))
    if not match:
        return None
    doc_raw = match.group("doc").strip().lower()
    doc_type = next((v for k, v in _DOC_TYPES.items() if k in doc_raw), "unknown")
    local = match.group("local")
    period = match.group("date").replace(".", "-")
    return {
        "s3_key": key,
        "union": _LOCAL_TO_UNION.get(local, f"local_{local}"),
        "local": local,
        "period": period,
        "doc_type": doc_type,
        "confidence": "high",
        "method": "filename",
    }


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], _context: LambdaContext) -> dict[str, Any]:
    """Classify an uploaded document. Event: ``{"s3_key": "..."}``."""
    try:
        key = event["s3_key"]
        result = _classify_by_filename(key)
        if result is None:
            # Ambiguous → would invoke Bedrock Haiku here (Spec/09 §4.2 step 3).
            logger.warning("filename classification ambiguous", extra={"s3_key": key})
            return {
                "s3_key": key,
                "doc_type": "unknown",
                "confidence": "low",
                "method": "needs_review",
            }
        logger.info("classified", extra=result)
        return result
    except Exception:
        logger.exception("classifier failed")
        raise
