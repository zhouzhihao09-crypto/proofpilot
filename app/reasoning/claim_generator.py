"""Claim generation interfaces and implementations.

Claim generation turns retrieved evidence into candidate claims that
can be verified. Phase 1 ships a deterministic generator so the whole
pipeline can run offline; an LLM-backed generator can be swapped in
behind the same interface.
"""

from __future__ import annotations

import abc
import re
from typing import Optional, Sequence

from app.llm.provider import LLMProvider, LLMRequest
from app.models import Chunk, Claim, VerificationStatus


class ClaimGenerator(abc.ABC):
    """Interface for generating candidate claims from evidence."""

    name: str = "base"

    @abc.abstractmethod
    def generate(self, task, evidence: Sequence[Chunk]) -> list[Claim]:
        raise NotImplementedError


class DeterministicClaimGenerator(ClaimGenerator):
    """Extracts candidate claims from evidence using pattern matching.

    This generator is deterministic and does not require an LLM. It looks
    for sentences that assert a relationship (e.g. "X has Y", "X satisfies
    Y", "X is Y") and turns them into candidate claims. Claims always
    start with INSUFFICIENT_EVIDENCE until the verification stage runs.
    """

    name = "deterministic"

    ASSERTION_PATTERNS = [
        r"(\b\w+(?:\s+\w+){0,4})\b\s+(is|are|has|have|holds?|satisfies?|meets?|provides?|includes?|uses?|follows?)\b((?:\s+\w+){1,6})",
    ]

    def __init__(self, max_claims_per_chunk: int = 3) -> None:
        self.max_claims_per_chunk = max_claims_per_chunk

    def generate(self, task, evidence: Sequence[Chunk]) -> list[Claim]:
        claims: list[Claim] = []
        seen: set[str] = set()
        for chunk in evidence:
            sentences = self._split_sentences(chunk.text)
            count = 0
            for sentence in sentences:
                if count >= self.max_claims_per_chunk:
                    break
                for pattern in self.ASSERTION_PATTERNS:
                    for match in re.finditer(pattern, sentence, flags=re.IGNORECASE):
                        subject = match.group(1).strip()
                        verb = match.group(2).strip()
                        predicate = match.group(3).strip()
                        if len(subject) < 3 or len(predicate) < 3:
                            continue
                        text = f"{subject} {verb} {predicate}".strip().rstrip(".;,")
                        key = text.lower()
                        if key in seen:
                            continue
                        seen.add(key)
                        claims.append(
                            Claim(
                                claim_id=f"claim_{len(claims) + 1:04d}",
                                task_id=task.task_id,
                                text=text,
                                verification_status=VerificationStatus.INSUFFICIENT_EVIDENCE,
                            )
                        )
                        count += 1
                        break
        return claims

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        # Split on sentence punctuation and on newlines so that label
        # lines (e.g. "Supplier: Company Alpha") do not merge with the
        # following assertion into a single sentence.
        raw = re.split(r"(?<=[.!?])\s+|\n+", text)
        return [s.strip() for s in raw if s.strip()]


class LLMClaimGenerator(ClaimGenerator):
    """Generates claims using an LLM provider.

    The LLM is given structured input (the task and evidence excerpts)
    and asked to return JSON. The deterministic generator is used as a
    fallback if the LLM is unavailable.
    """

    name = "llm"

    SYSTEM_PROMPT = (
        "You are an evidence analysis assistant. Generate candidate claims "
        "that could be verified against the provided evidence. Return JSON "
        "with a 'claims' array of objects, each with a 'text' field. "
        "Do not evaluate truth; only extract candidate claims."
    )

    def __init__(self, provider: LLMProvider, fallback: Optional[ClaimGenerator] = None) -> None:
        self.provider = provider
        self.fallback = fallback or DeterministicClaimGenerator()

    def generate(self, task, evidence: Sequence[Chunk]) -> list[Claim]:
        if evidence:
            evidence_block = "\n".join(
                f"[{i + 1}] {chunk.text[:400]}" for i, chunk in enumerate(evidence)
            )
            prompt = (
                f"Task: {task.question}\n\n"
                f"Evidence excerpts:\n{evidence_block}\n\n"
                "Generate candidate claims as JSON: {\"claims\": [{\"text\": \"...\"}]}"
            )
            try:
                from pydantic import BaseModel

                class ClaimsOutput(BaseModel):
                    claims: list[dict]

                result = self.provider.structured(
                    LLMRequest(prompt=prompt, system=self.SYSTEM_PROMPT),
                    ClaimsOutput,
                )
                claims: list[Claim] = []
                for index, item in enumerate(result.claims):
                    text = (item.get("text") if isinstance(item, dict) else getattr(item, "text", "")).strip()
                    if not text:
                        continue
                    claims.append(
                        Claim(
                            claim_id=f"claim_{index + 1:04d}",
                            task_id=task.task_id,
                            text=text,
                            verification_status=VerificationStatus.INSUFFICIENT_EVIDENCE,
                        )
                    )
                if claims:
                    return claims
            except Exception:
                pass
        return self.fallback.generate(task, evidence)