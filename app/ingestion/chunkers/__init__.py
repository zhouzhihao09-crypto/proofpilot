"""Text chunking interfaces and implementations.

Chunking splits extracted text into retrievable segments. A simple
fixed-size chunker with overlap is sufficient for Phase 1.
"""

from __future__ import annotations

import abc
import re
from typing import Optional

from app.models import Chunk


class Chunker(abc.ABC):
    """Interface for splitting text into chunks."""

    name: str = "base"

    @abc.abstractmethod
    def chunk(self, text: str, document_id: str) -> list[Chunk]:
        raise NotImplementedError


class FixedSizeChunker(Chunker):
    """Splits text into fixed-size character chunks with overlap."""

    name = "fixed_size"

    def __init__(
        self,
        chunk_size: int = 1000,
        overlap: int = 150,
        min_chunk_size: int = 50,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError("overlap must be non-negative and smaller than chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.min_chunk_size = min_chunk_size

    def chunk(self, text: str, document_id: str) -> list[Chunk]:
        # Normalize horizontal whitespace but preserve newlines so that
        # paragraph/line boundaries survive into chunk text. This keeps
        # downstream sentence splitting and claim extraction accurate.
        normalized = re.sub(r"[ \t]+", " ", text).strip()
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        if not normalized:
            return []

        chunks: list[Chunk] = []
        start = 0
        sequence = 0
        while start < len(normalized):
            end = min(start + self.chunk_size, len(normalized))
            piece = normalized[start:end].strip()
            if piece:
                chunk_id = f"{document_id}_chunk_{sequence:04d}"
                chunks.append(
                    Chunk(
                        chunk_id=chunk_id,
                        document_id=document_id,
                        text=piece,
                        start_char=start,
                        end_char=end,
                        sequence=sequence,
                        token_estimate=max(1, len(piece) // 4),
                    )
                )
                sequence += 1
            if end >= len(normalized):
                break
            start = end - self.overlap
        return chunks