"""Stage 1 - ingest / normalize. Read-only access to cba/ documents.

Pulls text + tables out of the union's CBA PDFs via pdfplumber. Image-only
pages (0 text, 0 tables) are reported so the extractor can blank+flag the
cells those pages would have sourced (the OCR/LLM boundary - no tesseract here).
"""
from __future__ import annotations

import os
import pdfplumber


def cba_files(union_dir: str):
    cba = os.path.join(union_dir, "cba")
    return sorted(
        os.path.join(cba, f) for f in os.listdir(cba) if f.lower().endswith(".pdf")
    )


def page_texts(pdf_path: str):
    """Return list of (page_index, text) for a PDF (text may be '' for image pages)."""
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, p in enumerate(pdf.pages):
            out.append((i, p.extract_text() or ""))
    return out


def full_text(pdf_path: str) -> str:
    return "\n".join(t for _, t in page_texts(pdf_path))


def extract_tables(pdf_path: str):
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for p in pdf.pages:
            for t in p.extract_tables():
                out.append(t)
    return out


def is_image_only(pdf_path: str) -> bool:
    """True if every page has no extractable text and no tables (needs OCR)."""
    with pdfplumber.open(pdf_path) as pdf:
        for p in pdf.pages:
            if (p.extract_text() or "").strip() or p.extract_tables():
                return False
    return True
