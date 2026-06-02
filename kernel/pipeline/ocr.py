"""Stage 1 OCR fallback - self-contained, no system binary, no API key.

The sprinkler_fitters_704 Rate Notice is image-only (0 text, 0 tables, 12
embedded images), so pdfplumber recovers nothing. This module renders each page
to a raster via `pypdfium2` and runs `rapidocr-onnxruntime` (a bundled ONNX OCR
model - no tesseract, no torch, no network at run time) to recover the
wage/fringe grid text and the box position of every token.

Run with the OCR deps added to the uv invocation, e.g.:
    uv run --with pdfplumber,openpyxl,pandas,pyyaml,pypdfium2,rapidocr-onnxruntime \
        python pipeline/run.py --union sprinkler_fitters_704

Returns, per page, a list of Token(y, x, text, conf). Cells are matched to their
labels by horizontal band (same y) - the notices are single-column key/value
sheets, so a value token shares a row with its label token.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_OCR = None


@dataclass
class Token:
    y: float          # top of bounding box (page raster pixels)
    x: float          # left of bounding box
    text: str
    conf: float


def _engine():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    return _OCR


def ocr_pages(pdf_path: str, scale: int = 3):
    """OCR every page; return list[list[Token]] (one list per page)."""
    import numpy as np
    import pypdfium2 as pdfium

    ocr = _engine()
    doc = pdfium.PdfDocument(pdf_path)
    pages = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            pil = page.render(scale=scale).to_pil().convert("RGB")
            result, _ = ocr(np.array(pil))
            toks = []
            for box, txt, conf in (result or []):
                ys = [pt[1] for pt in box]
                xs = [pt[0] for pt in box]
                toks.append(Token(min(ys), min(xs), txt, float(conf)))
            toks.sort(key=lambda t: (t.y, t.x))
            pages.append(toks)
    finally:
        doc.close()
    return pages


# --- number parsing helpers -------------------------------------------------

_NUM = re.compile(r"\$?\s*([0-9]*\.?[0-9]+)")


def first_number(text: str):
    """Extract the first numeric value from an OCR string, or None."""
    m = _NUM.search(text.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def value_on_row(tokens, label_token, x_min=900):
    """Return the numeric value token sharing label_token's horizontal band.

    The notices put the dollar figure in the right column (large x); we take the
    right-most numeric token within +/-25px of the label's vertical center.
    """
    yc = label_token.y
    cands = [t for t in tokens
             if abs(t.y - yc) <= 30 and t.x >= x_min and first_number(t.text) is not None]
    if not cands:
        return None
    cands.sort(key=lambda t: -t.x)
    return first_number(cands[0].text)
