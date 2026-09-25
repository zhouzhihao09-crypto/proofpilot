"""Ingestion service: coordinates extraction and chunking."""

from __future__ import annotations

import hashlib
from typing import Optional, Sequence

from app.ingestion.chunkers import Chunker, FixedSizeChunker
from app.ingestion.extractors import PdfTextExtractor, TextExtractor
from app.models import Chunk, Document, DocumentStatus


class IngestionService:
    """Ingests raw document bytes and produces chunks."""

    def __init__(
        self,
        extractor: Optional[TextExtractor] = None,
        chunker: Optional[Chunker] = None,
    ) -> None:
        self.extractor = extractor or PdfTextExtractor()
        self.chunker = chunker or FixedSizeChunker()

    def ingest(self, filename: str, data: bytes, mime_type: str = "application/pdf") -> tuple[Document, list[Chunk]]:
        """Extract text and chunk it, returning the document and chunks."""
        document_id = self._document_id(filename, data)
        document = Document(
            document_id=document_id,
            filename=filename,
            mime_type=mime_type,
            status=DocumentStatus.EXTRACTING,
        )

        try:
            text = self.extractor.extract(data)
            document.status = DocumentStatus.CHUNKED
        except Exception as exc:  # pragma: no cover - defensive
            document.status = DocumentStatus.FAILED
            document.metadata["error"] = str(exc)
            return document, []

        chunks = self.chunker.chunk(text, document_id=document_id)
        document.status = DocumentStatus.CHUNKED
        document.page_count = text.count("\n")  # rough proxy, updated by extractor if needed
        return document, chunks

    @staticmethod
    def _document_id(filename: str, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()[:12]
        safe_name = "".join(c if c.isalnum() else "_" for c in filename)[:40]
        return f"doc_{safe_name}_{digest}"