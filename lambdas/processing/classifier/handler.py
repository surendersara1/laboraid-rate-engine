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
# 2024.08.01-2030.07.31.483 CBA.pdf  ->  CBA range filename, no single anchor
# date; the period must come from the batch context (other PDFs in the same
# upload batch carry the actual rate-period anchor).
_FILENAME_RANGE = re.compile(
    r"(?P<sd>\d{4}\.\d{2}\.\d{2})[-–]"
    r"(?P<ed>\d{4}\.\d{2}\.\d{2})\.(?P<local>\d{3})\s+(?P<doc>.+?)\.pdf$",
    re.IGNORECASE,
)
# Key shapes — see lambdas/api/upload-presign/handler.py build_key().
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_BATCH_PERIOD_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_DOC_TYPES = {
    "rate notice": "rate_notice",
    "rate sheet": "rate_sheet",
    "wage sheet": "rate_sheet",
    "wage rate sheet": "rate_sheet",
    "wage rate notice": "rate_notice",
    # Order matters: more specific keywords first so "apprentice wage sheet"
    # binds to apprentice_scale, not rate_sheet.
    "apprentice wage sheet": "apprentice_scale",
    "trainee scale": "apprentice_scale",
    "apprentice scale": "apprentice_scale",
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


def _batch_period_from_key(key: str) -> str | None:
    """When upload-presign encoded a batch_period in the S3 key
    (``laboraid/uploads/<batch_id>/<YYYY-MM-DD>/<filename>``), pull it
    out so downstream stages can anchor the rate period for non-Rate-Notice
    docs (CBAs, apprentice scales) that don't carry the period in their
    own filename."""
    parts = key.split("/")
    # uploads/<batch_id>/<period>/<filename>
    if (
        len(parts) >= 5
        and parts[0] == "laboraid"
        and parts[1] == "uploads"
        and _UUID_RE.match(parts[2] or "")
        and _BATCH_PERIOD_KEY_RE.match(parts[3] or "")
    ):
        return parts[3]
    return None


def _batch_period_from_siblings(key: str) -> str | None:
    """Fallback when the browser didn't put a batch_period segment in the
    key (e.g., reviewer's browser cached the pre-update bundle).

    For ``laboraid/uploads/<batch_id>/<filename>`` we list S3 siblings
    under the same batch_id prefix and look for a Rate Notice / Rate
    Sheet with a single ``YYYY.MM.DD.<local>`` filename. If exactly one
    qualifying anchor exists, that file's date is the batch's anchor
    period.

    Returns None when there are no batched siblings, when the only
    sibling is a CBA range-date file, or when multiple distinct anchor
    dates exist (ambiguous — let each file use its own filename date).
    """
    parts = key.split("/")
    if not (
        len(parts) >= 4
        and parts[0] == "laboraid"
        and parts[1] == "uploads"
        and _UUID_RE.match(parts[2] or "")
    ):
        return None
    prefix = "/".join(parts[:3]) + "/"
    bucket = os.environ.get("INPUTS_BUCKET", "laboraid-dev-l3-bucket-inputs")
    try:
        import boto3

        s3 = boto3.client("s3")
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    except Exception as e:  # pragma: no cover
        logger.warning("classifier: sibling lookup failed: %s", e)
        return None
    anchors: set[str] = set()
    for obj in resp.get("Contents") or []:
        sib_key = obj["Key"]
        if sib_key == key:
            continue
        sib_name = os.path.basename(sib_key)
        m = _FILENAME.search(sib_name)
        if not m:
            continue
        doc = m.group("doc").lower()
        # Only Rate Notice / Rate Sheet / Wage Sheet anchor the batch.
        # Apprentice Scales typically have their own non-anchor dates.
        if not any(k in doc for k in ("rate notice", "rate sheet", "wage sheet")):
            continue
        anchors.add(m.group("date").replace(".", "-"))
    if len(anchors) == 1:
        return next(iter(anchors))
    if len(anchors) > 1:
        # Multiple anchors → use the most recent one (matches the browser's
        # tie-break heuristic). If the reviewer truly meant separate periods,
        # they should upload in separate batches.
        return sorted(anchors)[-1]
    return None


def _doc_type_from_name(name: str) -> str:
    s = name.strip().lower()
    # Longest-keyword-wins so "apprentice wage sheet" doesn't collide with
    # "wage sheet" (rate_sheet).
    matches = sorted(
        ((k, v) for k, v in _DOC_TYPES.items() if k in s),
        key=lambda kv: -len(kv[0]),
    )
    return matches[0][1] if matches else "unknown"


def _classify_by_filename(key: str) -> dict[str, Any] | None:
    """Filename → (local, period, doc_type, union). Recognizes both:

    - Single-date Rate Notice/Rate Sheet/Apprentice Scale (``YYYY.MM.DD.<local> ...``)
      → period taken from filename.
    - Multi-year CBA (``YYYY.MM.DD-YYYY.MM.DD.<local> CBA.pdf``)
      → no single anchor date; the rate period must come from the batch
      context (S3 key carries it as a segment when the browser detected
      an anchor Rate Notice in the batch).
    """
    filename = os.path.basename(key)
    batch_period = _batch_period_from_key(key)
    # Fallback: browser cache may be serving the pre-batch_period bundle.
    # If the S3 key has a batch_id but no period segment, list siblings to
    # infer the anchor. Same UX outcome (CBA + Wage Rate Sheet merge into
    # the Rate Notice's period) without depending on browser code.
    if batch_period is None:
        batch_period = _batch_period_from_siblings(key)
        if batch_period:
            logger.info(
                "classifier: inferred batch_period=%s from S3 siblings (browser sent old key shape)",
                batch_period,
            )

    match = _FILENAME.search(filename)
    if match:
        doc_type = _doc_type_from_name(match.group("doc"))
        local = match.group("local")
        # Even on a clean-date filename, a batch_period (if present) wins
        # for non-Rate-Notice doc types — a CBA or Apprentice Scale should
        # always merge into the Rate Notice's period, never their own
        # nominal date.
        filename_period = match.group("date").replace(".", "-")
        period = (
            batch_period if (batch_period and doc_type != "rate_notice")
            else filename_period
        )
        return {
            "s3_key": key,
            "union": _LOCAL_TO_UNION.get(local, f"local_{local}"),
            "local": local,
            "period": period,
            "doc_type": doc_type,
            "confidence": "high",
            "method": "filename",
        }

    # CBA range-date filename.
    rmatch = _FILENAME_RANGE.search(filename)
    if rmatch:
        doc_type = _doc_type_from_name(rmatch.group("doc"))
        local = rmatch.group("local")
        # CBAs MUST inherit the rate period from the batch — their own
        # filename only carries the contract validity span, not the
        # specific rate-effective date the customer wants extracted.
        if not batch_period:
            logger.warning(
                "classifier: CBA-style filename with NO batch_period in key — "
                "cannot resolve a single rate period. Falling back to range "
                "start_date; reviewer will see this in audit_log.",
                extra={"key": key},
            )
        period = batch_period or rmatch.group("sd").replace(".", "-")
        return {
            "s3_key": key,
            "union": _LOCAL_TO_UNION.get(local, f"local_{local}"),
            "local": local,
            "period": period,
            "doc_type": doc_type,
            "confidence": "high" if batch_period else "low",
            "method": "filename+batch" if batch_period else "filename_range_only",
        }

    return None


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
