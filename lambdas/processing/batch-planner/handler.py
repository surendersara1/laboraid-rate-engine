"""Batch planner Lambda — Stage 0 of the SEQUENTIAL batch pipeline.

Replaces the old per-S3-object parallel trigger. Given a whole batch manifest
(every PDF the reviewer staged + pressed "Process"), this classifies each file
and returns a DETERMINISTIC processing order so the downstream Map state can
apply them one-at-a-time, in the order Dan works by hand:

    1. CBA(s)                       — establish structure / base rates
    2. rate notices / wage sheets   — by effective date ASCENDING (oldest first,
       apprentice scales                newest applied LAST so it wins)

Upload order is irrelevant — the reviewer can stage CBA + 2026 + 2024 + 2025 in
any order; the planner sorts them CBA -> 2024 -> 2025 -> 2026.

Input (from the batch-process API):
  {
    "batch_id": "...",
    "batch_period": "2026-01-01",
    "files": [{"s3_key": "...", "filename": "..."}, ...]
  }

Output (consumed by the SFN Map):
  {
    "batch_id": "...",
    "local": "704",
    "period": "2026-01-01",
    "docs": [ {s3_key, filename, union, local, period, doc_type, effective_date,
               order_index}, ... ]   # already in processing order
  }
"""
from __future__ import annotations

import os
from typing import Any

try:  # pragma: no cover - present in the Lambda runtime
    from aws_lambda_powertools import Logger, Tracer

    logger = Logger(service="laboraid-batch-planner")
    tracer = Tracer()

    def _instrument(fn: Any) -> Any:
        return logger.inject_lambda_context(tracer.capture_lambda_handler(fn))

except ModuleNotFoundError:  # pragma: no cover - offline unit-test env
    import logging

    logger = logging.getLogger("laboraid-batch-planner")

    def _instrument(fn: Any) -> Any:
        return fn


CLASSIFIER_FN = os.environ.get("CLASSIFIER_FN", "laboraid-dev-l4-fn-classifier")

# Processing precedence by doc type: lower number = processed EARLIER.
# CBA(s) first (base), then rate documents by effective date, apprentice scales
# alongside their rate documents. Unknown docs go last so they can't clobber
# a confidently-classified rate notice.
_DOC_ORDER = {
    "cba": 0,
    "apprentice_scale": 1,
    "rate_sheet": 1,
    "rate_notice": 1,
    "unknown": 2,
}


def _classify_one(s3_key: str) -> dict[str, Any]:
    """Invoke the existing classifier Lambda for one file. Single source of
    truth for doc_type / local / period / effective-date extraction."""
    import json
    import boto3

    lc = boto3.client("lambda")
    resp = lc.invoke(
        FunctionName=CLASSIFIER_FN,
        InvocationType="RequestResponse",
        Payload=json.dumps({"s3_key": s3_key}).encode("utf-8"),
    )
    body = resp["Payload"].read()
    if resp.get("FunctionError"):
        raise RuntimeError(
            f"classifier FunctionError for {s3_key}: {body[:300].decode('utf-8', 'replace')}"
        )
    return json.loads(body.decode("utf-8")) if body else {}


def _effective_date(classify: dict[str, Any], filename: str) -> str:
    """The date used for ORDERING non-CBA docs. The classifier's `period` is
    the resolved rate period; for a rate notice that equals its own effective
    date (from the filename), which is exactly what we sort on. Fall back to a
    filename date scan, then to a far-past sentinel so unknowns sort first
    within their (last) group rather than crashing."""
    p = (classify.get("period") or "").strip()
    if len(p) == 10 and p[4] == "-" and p[7] == "-":
        return p
    # crude filename scan: first YYYY.MM.DD
    import re

    m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", filename or "")
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return "0001-01-01"


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    batch_id = event.get("batch_id") or ""
    batch_period = (event.get("batch_period") or "").strip()
    files = event.get("files") or []
    if not files:
        raise ValueError("batch-planner: empty files manifest")

    planned: list[dict[str, Any]] = []
    local = None
    for f in files:
        s3_key = f.get("s3_key") or f.get("key") or ""
        filename = f.get("filename") or (s3_key.rsplit("/", 1)[-1] if s3_key else "")
        if not s3_key:
            logger.warning("batch-planner: file with no s3_key skipped: %s", f)
            continue
        classify = _classify_one(s3_key)
        doc_type = (classify.get("doc_type") or "unknown").lower()
        eff = _effective_date(classify, filename)
        local = local or classify.get("local")
        planned.append({
            "s3_key": s3_key,
            "filename": filename,
            "union": classify.get("union") or "",
            "local": classify.get("local") or "",
            # Every doc resolves into the SAME rate period (the batch anchor).
            # The classifier already applies this for non-rate-notice docs;
            # enforce it here so the whole batch shares one period_id in Aurora.
            "period": classify.get("period") or batch_period,
            "doc_type": doc_type,
            "effective_date": eff,
            "classify_method": classify.get("method") or "",
        })

    # Sort: doc-order group (CBA first), then effective date ASC, then filename
    # for a stable tie-break.
    planned.sort(key=lambda d: (
        _DOC_ORDER.get(d["doc_type"], 2),
        d["effective_date"],
        d["filename"],
    ))
    for i, d in enumerate(planned):
        d["order_index"] = i

    # Anchor period: prefer the batch_period; else the latest rate doc's period.
    period = batch_period
    if not period:
        rate_periods = [d["period"] for d in planned if d["doc_type"] != "cba" and d["period"]]
        period = max(rate_periods) if rate_periods else (planned[0]["period"] if planned else "")

    result = {
        "batch_id": batch_id,
        "local": local or (planned[0]["local"] if planned else ""),
        "period": period,
        "doc_count": len(planned),
        "docs": planned,
    }
    logger.info(
        "batch-planner: local=%s period=%s order=%s",
        result["local"], period,
        [f"{d['doc_type']}@{d['effective_date']}" for d in planned],
    )
    return result
