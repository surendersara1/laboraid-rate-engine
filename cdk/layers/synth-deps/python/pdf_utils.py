"""PDF helpers for Bedrock document blocks.

Bedrock caps a single PDF document block at 100 pages, but a message may carry
several blocks. ``pdf_to_chunks`` splits an oversized PDF into <=100-page
chunks so large CBAs (2025-2030 agreements run well past 100 pages) onboard and
extract instead of crashing.
"""
from __future__ import annotations

import io

_MAX_PAGES = 100


def page_count(pdf_bytes: bytes) -> int:
    try:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(pdf_bytes)).pages)
    except Exception:
        return 1


def first_pages(pdf_bytes: bytes, n: int = _MAX_PAGES) -> bytes:
    """Return a PDF of the first n pages (structure lives in the early articles
    of a CBA). Returns the original if it's already within n / unparseable."""
    try:
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(io.BytesIO(pdf_bytes))
        if len(reader.pages) <= n:
            return pdf_bytes
        writer = PdfWriter()
        for i in range(n):
            writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()
    except Exception:
        return pdf_bytes


def pdf_to_chunks(pdf_bytes: bytes, max_pages: int = _MAX_PAGES) -> list[bytes]:
    """Return the PDF as one or more <=max_pages chunks. If the PDF is already
    within the limit (or can't be parsed), returns ``[pdf_bytes]`` unchanged."""
    try:
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(io.BytesIO(pdf_bytes))
        n = len(reader.pages)
        if n <= max_pages:
            return [pdf_bytes]
        chunks: list[bytes] = []
        for start in range(0, n, max_pages):
            writer = PdfWriter()
            for i in range(start, min(start + max_pages, n)):
                writer.add_page(reader.pages[i])
            buf = io.BytesIO()
            writer.write(buf)
            chunks.append(buf.getvalue())
        return chunks
    except Exception:
        # Unparseable / pypdf missing — hand back the original; the caller's
        # Bedrock call will surface a clear error if it's genuinely too big.
        return [pdf_bytes]
