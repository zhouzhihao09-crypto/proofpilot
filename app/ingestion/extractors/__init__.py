"""Document extraction interfaces and implementations.

Extraction is the first stage of the pipeline: raw bytes -> text.
"""

from __future__ import annotations

import abc
from typing import Optional


class TextExtractor(abc.ABC):
    """Interface for extracting text from a raw document."""

    name: str = "base"

    @abc.abstractmethod
    def extract(self, data: bytes) -> str:
        """Extract plain text from raw document bytes."""
        raise NotImplementedError

    def supports(self, mime_type: str) -> bool:
        """Return True if this extractor handles the given mime type."""
        return False


class PdfTextExtractor(TextExtractor):
    """Extracts text from PDF documents using pypdf.

    The implementation is intentionally simple: it concatenates page
    text and records page boundaries. PDFs are treated as untrusted
    data; no embedded instructions are executed.
    """

    name = "pypdf"

    def __init__(self, max_pages: Optional[int] = None) -> None:
        self.max_pages = max_pages

    def supports(self, mime_type: str) -> bool:
        return mime_type.lower() in ("application/pdf", ".pdf")

    def extract(self, data: bytes) -> str:
        from pypdf import PdfReader
        from io import BytesIO

        reader = PdfReader(BytesIO(data))
        pages: list[str] = []
        page_count = len(reader.pages)
        limit = page_count if self.max_pages is None else min(self.max_pages, page_count)
        for index in range(limit):
            page_text = reader.pages[index].extract_text() or ""
            pages.append(page_text)
        return "\n".join(pages)