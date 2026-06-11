"""OCR pre-processing Lambda — first stage of the SFN main pipeline.

Per the LaborAid integration brief (docs/Design/client_brief_and_integration_plan.md):
the customer ships a mix of clean digital PDFs (text layer present) and scanned
faxes/photocopies (image-only). Bedrock Claude vision can OCR scanned tables but
degrades on small fonts and crowded grids — exactly the shape of a union rate
notice. Textract `analyze_document` with FORMS+TABLES is purpose-built for this
and returns cell-level structure with bounding boxes.

Strategy:
  1. Download the PDF from the inputs bucket.
  2. Detect whether the PDF already has a text layer (pypdf metadata + extracted
     character count). If it does — skip Textract, write a `layout.json` that
     says so, return.
  3. If not — call Textract synchronously for <= TEXTRACT_SYNC_PAGE_LIMIT pages,
     otherwise StartDocumentAnalysis (async) and poll. Save the full Textract
     response to S3 next to the PDF as `<key>.layout.json`. The llm-extractor
     downstream reads this and feeds tables to Claude as structured context.

Input shape (called by SFN as the first step on every upload):
  {
    "s3_key": "<inputs-bucket key for the PDF>",   # required
    "bucket": "<inputs-bucket name>",              # optional, falls back to env
  }

Output shape (merged into SFN state):
  {
    "ocr": {
      "method": "text_layer_present" | "textract_sync" | "textract_async",
      "layout_s3_key": "<key>.layout.json" | null,
      "page_count": <int>,
      "text_chars_sampled": <int>,
      "duration_ms": <int>,
      "table_count": <int>,
      "kv_count": <int>
    }
  }
"""

from __future__ import annotations

import io
import json
import os
import time
from typing import Any

try:  # pragma: no cover - present in the Lambda runtime
    from aws_lambda_powertools import Logger, Tracer

    logger = Logger(service="laboraid-ocr-preprocess")
    tracer = Tracer()

    def _instrument(fn: Any) -> Any:
        return logger.inject_lambda_context(tracer.capture_lambda_handler(fn))

except ModuleNotFoundError:  # pragma: no cover - offline unit-test env
    import logging

    logger = logging.getLogger("laboraid-ocr-preprocess")

    def _instrument(fn: Any) -> Any:
        return fn


INPUTS_BUCKET = os.environ.get("INPUTS_BUCKET", "laboraid-dev-l3-bucket-inputs")
OUTPUTS_BUCKET = os.environ.get("OUTPUTS_BUCKET", "laboraid-dev-l3-bucket-outputs")
TEXTRACT_SYNC_PAGE_LIMIT = int(os.environ.get("TEXTRACT_SYNC_PAGE_LIMIT", "5"))
TEXTRACT_ASYNC_POLL_SECONDS = float(os.environ.get("TEXTRACT_ASYNC_POLL_SECONDS", "3"))
TEXTRACT_ASYNC_TIMEOUT_SECONDS = int(os.environ.get("TEXTRACT_ASYNC_TIMEOUT_SECONDS", "780"))
# How many characters of extractable text we consider "enough" to call it a
# text-layer PDF and skip Textract. A few page numbers extracted from an
# otherwise scanned doc shouldn't count.
TEXT_LAYER_CHAR_THRESHOLD = int(os.environ.get("TEXT_LAYER_CHAR_THRESHOLD", "200"))


def _detect_text_layer(pdf_bytes: bytes) -> tuple[bool, int, int]:
    """Return (has_text_layer, total_pages, sampled_char_count).

    Reads the PDF with pypdf and concatenates extracted text from up to the
    first 5 pages. If the total character count is >= TEXT_LAYER_CHAR_THRESHOLD
    we consider it a digital PDF and skip Textract.

    On any pypdf failure (encrypted PDFs, unsupported font subsets, malformed
    streams) we fail OPEN — assume the PDF has a text layer rather than send a
    million-page CBA to Textract async. The cost asymmetry is huge: a
    misclassified digital PDF wastes nothing (Claude reads PDFs natively); a
    misclassified scanned PDF means Claude vision instead of Textract — still
    works, just slightly weaker on dense tables.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("pypdf not present — assuming text layer present (skip Textract)")
        return True, 0, 0
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        n_pages = len(reader.pages)
        sampled = ""
        for page in reader.pages[: min(5, n_pages)]:
            try:
                sampled += page.extract_text() or ""
            except Exception:  # noqa: BLE001 — pypdf occasionally raises on edge fonts
                continue
        chars = len(sampled.strip())
        return chars >= TEXT_LAYER_CHAR_THRESHOLD, n_pages, chars
    except Exception:  # noqa: BLE001 — encrypted / malformed PDFs
        logger.exception("pypdf failed — assuming text layer present (skip Textract)")
        return True, 0, 0


def _textract_sync(bucket: str, key: str) -> dict[str, Any]:
    import boto3

    tx = boto3.client("textract")
    return tx.analyze_document(
        Document={"S3Object": {"Bucket": bucket, "Name": key}},
        FeatureTypes=["FORMS", "TABLES"],
    )


def _textract_async(bucket: str, key: str) -> dict[str, Any]:
    """Start an async job, poll until SUCCEEDED, then page through all blocks."""
    import boto3

    tx = boto3.client("textract")
    job = tx.start_document_analysis(
        DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}},
        FeatureTypes=["FORMS", "TABLES"],
    )
    job_id = job["JobId"]
    deadline = time.time() + TEXTRACT_ASYNC_TIMEOUT_SECONDS
    while time.time() < deadline:
        status_resp = tx.get_document_analysis(JobId=job_id, MaxResults=1)
        status = status_resp["JobStatus"]
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "PARTIAL_SUCCESS"):
            raise RuntimeError(f"Textract async job {job_id} ended with {status}")
        time.sleep(TEXTRACT_ASYNC_POLL_SECONDS)
    else:
        raise TimeoutError(f"Textract async job {job_id} did not complete in budget")

    all_blocks: list[dict[str, Any]] = []
    next_token: str | None = None
    document_metadata: dict[str, Any] = {}
    while True:
        kw: dict[str, Any] = {"JobId": job_id, "MaxResults": 1000}
        if next_token:
            kw["NextToken"] = next_token
        page = tx.get_document_analysis(**kw)
        all_blocks.extend(page.get("Blocks") or [])
        document_metadata = page.get("DocumentMetadata") or document_metadata
        next_token = page.get("NextToken")
        if not next_token:
            break
    return {"Blocks": all_blocks, "DocumentMetadata": document_metadata}


def _summarize(textract: dict[str, Any]) -> dict[str, int]:
    blocks = textract.get("Blocks") or []
    return {
        "table_count": sum(1 for b in blocks if b.get("BlockType") == "TABLE"),
        "kv_count": sum(1 for b in blocks if b.get("BlockType") == "KEY_VALUE_SET"),
        "line_count": sum(1 for b in blocks if b.get("BlockType") == "LINE"),
        "page_count": (textract.get("DocumentMetadata") or {}).get("Pages", 0),
    }


@_instrument
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Run OCR pre-processing on a single PDF upload.

    Handles two SFN-state shapes:
      * EventBridge S3 event (raw):  {"detail": {"object": {"key": "..."}}}
      * Already-mapped:              {"s3_key": "...", "bucket": "..."}
    """
    started = time.time()
    bucket = event.get("bucket") or INPUTS_BUCKET
    s3_key = event.get("s3_key")
    if not s3_key:
        detail = event.get("detail") or {}
        s3_key = (detail.get("object") or {}).get("key", "")
        bucket = (detail.get("bucket") or {}).get("name", "") or bucket
    if not s3_key:
        raise ValueError("ocr-preprocess: missing s3_key / detail.object.key")
    if not s3_key.lower().endswith(".pdf"):
        logger.info("ocr-preprocess: %s is not a PDF — skipping", s3_key)
        return {
            "ocr": {
                "method": "skipped_non_pdf",
                "layout_s3_key": None,
                "duration_ms": int((time.time() - started) * 1000),
            }
        }

    import boto3

    s3 = boto3.client("s3")
    obj = s3.get_object(Bucket=bucket, Key=s3_key)
    pdf_bytes = obj["Body"].read()
    has_text, n_pages, chars = _detect_text_layer(pdf_bytes)
    logger.info(
        "ocr-preprocess: %s pages=%d text_chars=%d has_text=%s",
        s3_key, n_pages, chars, has_text,
    )

    layout_key = f"{s3_key}.layout.json"

    if has_text:
        # Digital PDF — write a small marker so downstream sees a uniform shape.
        marker = {
            "method": "text_layer_present",
            "page_count": n_pages,
            "text_chars_sampled": chars,
            "blocks": [],
            "tables": [],
        }
        s3.put_object(
            Bucket=OUTPUTS_BUCKET,
            Key=layout_key,
            Body=json.dumps(marker).encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="aws:kms",
        )
        return {
            "ocr": {
                "method": "text_layer_present",
                "layout_s3_key": layout_key,
                "page_count": n_pages,
                "text_chars_sampled": chars,
                "table_count": 0,
                "kv_count": 0,
                "duration_ms": int((time.time() - started) * 1000),
            }
        }

    # Scanned PDF — run Textract. The sync analyze_document API only supports
    # *single-page* PDFs; multi-page PDFs MUST go through StartDocumentAnalysis.
    # We only take the sync path when pypdf reported exactly one page.
    method = "textract_sync" if n_pages == 1 else "textract_async"
    try:
        if method == "textract_sync":
            result = _textract_sync(bucket, s3_key)
        else:
            result = _textract_async(bucket, s3_key)
    except Exception as exc:  # noqa: BLE001 — surface the failure to the SFN state but don't fail the pipeline
        logger.exception("ocr-preprocess: Textract failed for %s — falling back to vision-only", s3_key)
        return {
            "ocr": {
                "method": "textract_failed",
                "layout_s3_key": None,
                "page_count": n_pages,
                "text_chars_sampled": chars,
                "table_count": 0,
                "kv_count": 0,
                "error": str(exc)[:500],
                "duration_ms": int((time.time() - started) * 1000),
            }
        }
    summary = _summarize(result)

    # Persist the full Textract response next to the PDF in OUTPUTS_BUCKET.
    s3.put_object(
        Bucket=OUTPUTS_BUCKET,
        Key=layout_key,
        Body=json.dumps(result, default=str).encode("utf-8"),
        ContentType="application/json",
        ServerSideEncryption="aws:kms",
    )

    return {
        "ocr": {
            "method": method,
            "layout_s3_key": layout_key,
            "page_count": summary["page_count"] or n_pages,
            "text_chars_sampled": chars,
            "table_count": summary["table_count"],
            "kv_count": summary["kv_count"],
            "duration_ms": int((time.time() - started) * 1000),
        }
    }
