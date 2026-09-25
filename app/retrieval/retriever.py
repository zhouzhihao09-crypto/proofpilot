"""Retrieval interfaces and implementations.

Phase 1 uses a deterministic keyword-based retriever. This keeps the
system offline and testable without external dependencies. A vector
backend can be swapped in later behind the same interface.
"""

from __future__ import annotations

import abc
import math
import re
from collections import Counter
from typing import Optional, Sequence

from app.models import Chunk


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-zA-Z0-9]+", text.lower()) if t]


class Retriever(abc.ABC):
    """Interface for retrieving candidate evidence chunks for a query."""

    name: str = "base"

    @abc.abstractmethod
    def retrieve(self, query: str, top_k: int = 5) -> list[tuple[Chunk, float]]:
        """Return chunks scored against the query, highest first."""
        raise NotImplementedError


class KeywordRetriever(Retriever):
    """A simple BM25-style keyword retriever.

    Deterministic and dependency-free, which makes it ideal for tests
    and offline operation.
    """

    name = "keyword_bm25"

    def __init__(
        self,
        k1: float = 1.5,
        b: float = 0.75,
        min_score: float = 0.0,
    ) -> None:
        self.k1 = k1
        self.b = b
        self.min_score = min_score
        self._chunks: list[Chunk] = []
        self._doc_freq: dict[str, int] = {}
        self._avgdl: float = 0.0
        self._total_docs: int = 0

    def index(self, chunks: Sequence[Chunk]) -> None:
        self._chunks = list(chunks)
        self._doc_freq = {}
        total_len = 0
        for chunk in self._chunks:
            tokens = set(_tokenize(chunk.text))
            total_len += len(_tokenize(chunk.text))
            for token in tokens:
                self._doc_freq[token] = self._doc_freq.get(token, 0) + 1
        self._total_docs = len(self._chunks)
        self._avgdl = total_len / self._total_docs if self._total_docs else 0.0

    def retrieve(self, query: str, top_k: int = 5) -> list[tuple[Chunk, float]]:
        if not self._chunks:
            return []
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        scored: list[tuple[Chunk, float]] = []
        for chunk in self._chunks:
            score = self._bm25_score(query_tokens, chunk)
            if score > self.min_score:
                scored.append((chunk, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]

    def _bm25_score(self, query_tokens: list[str], chunk: Chunk) -> float:
        tokens = _tokenize(chunk.text)
        if not tokens:
            return 0.0
        freq = Counter(tokens)
        dl = len(tokens)
        score = 0.0
        for token in query_tokens:
            tf = freq.get(token, 0)
            if tf == 0:
                continue
            df = self._doc_freq.get(token, 0)
            idf = math.log(1 + (self._total_docs - df + 0.5) / (df + 0.5))
            denom = tf + self.k1 * (1 - self.b + self.b * dl / self._avgdl)
            score += idf * (tf * (self.k1 + 1)) / denom
        return score