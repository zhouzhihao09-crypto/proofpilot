"""Deterministic fake LLM provider for tests.

This provider returns predetermined structured responses based on the
prompt content. It never makes network calls, requires no API key, and is
fully deterministic. It is used exclusively by the test suite to exercise
the semantic verification path without depending on a real language model.

The provider inspects the CLAIM and EVIDENCE text inside the prompt and
returns a fixed response based on simple keyword rules. It is intentionally
primitive: it is a test double, not a semantic engine.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from pydantic import BaseModel, Field

from app.llm.provider import LLMProvider, LLMRequest, LLMResponse


class FakeLLMResponse(BaseModel):
    """Structured response shape returned by the fake provider."""

    status: str
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class FakeLLMProvider(LLMProvider):
    """A deterministic LLM provider used for tests.

    The provider matches the CLAIM text against a set of registered rules.
    Each rule is a (substring, status, explanation, confidence) tuple. The
    first matching rule wins. If no rule matches, the provider returns
    INSUFFICIENT_EVIDENCE by default.

    Matching is stem-based: "certified" matches "certification" and
    "certify" so the rule applies to related word forms.

    This is intentionally simple: it is a test double, not a real semantic
    verifier. Real semantic verification requires a capable language model.
    """

    name = "fake"

    def __init__(
        self,
        rules: Optional[list[tuple[str, str, str, float]]] = None,
        default_status: str = "INSUFFICIENT_EVIDENCE",
        default_confidence: float = 0.0,
        fail: bool = False,
    ) -> None:
        self.rules = rules or []
        self.default_status = default_status
        self.default_confidence = default_confidence
        self.fail = fail
        # Record the last prompt for test introspection.
        self.last_prompt: Optional[str] = None

    def add_rule(self, substring: str, status: str, explanation: str, confidence: float) -> None:
        """Register a response rule matched by substring in the CLAIM text."""
        self.rules.append((substring.lower(), status, explanation, confidence))

    @staticmethod
    def _stem(word: str) -> str:
        """Reduce a word to a rough stem for matching related forms."""
        w = word
        for suffix in ("tion", "sion", "ment", "ance", "ence", "ing", "ed", "ate", "ize", "s"):
            if w.endswith(suffix) and len(w) - len(suffix) >= 4:
                w = w[: -len(suffix)]
                break
        return w

    def _matches_rule(self, claim_lower: str, substring: str) -> bool:
        """True if substring or its stem appears in the claim text."""
        if substring in claim_lower:
            return True
        # Try stem matching so "certified" matches "certification".
        sub_stem = self._stem(substring)
        if len(sub_stem) < 4:
            return False
        for token in re.findall(r"[a-zA-Z0-9]+", claim_lower):
            if self._stem(token) == sub_stem:
                return True
            if sub_stem in token or token in sub_stem:
                return True
        return False

    def _extract_claim_text(self, prompt: str) -> str:
        for line in prompt.splitlines():
            if line.startswith("CLAIM:"):
                return line[len("CLAIM:"):].strip()
        return ""

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.last_prompt = request.prompt
        if self.fail:
            raise RuntimeError("FakeLLMProvider configured to fail")

        claim_text = self._extract_claim_text(request.prompt)
        claim_lower = claim_text.lower()
        for substring, status, explanation, confidence in self.rules:
            if self._matches_rule(claim_lower, substring):
                return LLMResponse(
                    content=json.dumps(
                        {
                            "status": status,
                            "explanation": explanation,
                            "evidence_ids": [],
                            "semantic_confidence": confidence,
                        }
                    ),
                    model="fake-1.0",
                    usage={"prompt_tokens": 0, "completion_tokens": 0},
                )

        return LLMResponse(
            content=json.dumps(
                {
                    "status": self.default_status,
                    "explanation": "No matching rule; default fake response.",
                    "evidence_ids": [],
                    "semantic_confidence": self.default_confidence,
                }
            ),
            model="fake-1.0",
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )

    def structured(self, request: LLMRequest, schema: type[BaseModel]) -> BaseModel:
        """Parse the LLM response and inject claim_id from the prompt.

        The fake provider returns JSON without claim_id (real LLMs would not
        know it). We extract it from the CLAIM line and inject it before
        validation so the structured result is complete.
        """
        response = self.complete(request)
        text = response.content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM did not return valid JSON: {text[:200]}") from exc
        # Inject claim_id from the CLAIM line so the result is valid.
        if "claim_id" not in data:
            data["claim_id"] = "llm_claim"
        return schema.model_validate(data)