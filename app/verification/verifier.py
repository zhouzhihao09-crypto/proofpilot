"""Verification interfaces and implementations.

Verification is an explicit pipeline stage that decides whether evidence
supports, contradicts, or is insufficient for a claim.

The verifier never simply asks an LLM "is this true?". It receives
structured inputs (claim + evidence) and returns a structured result.
"""

from __future__ import annotations

import abc
import re
from typing import Optional, Sequence

from app.models import Claim, Evidence, VerificationMethod, VerificationStatus, VerificationResult


class Verifier(abc.ABC):
    """Interface for verifying a claim against evidence."""

    name: str = "base"

    @abc.abstractmethod
    def verify(self, claim: Claim, evidence: Sequence[Evidence]) -> VerificationResult:
        """Return a structured verification result for the claim."""
        raise NotImplementedError


def _tokenize(text: str) -> set:
    return {t for t in re.findall(r"[a-zA-Z0-9]+", text.lower()) if t}


def _split_sentences(text: str) -> list:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


class DeterministicVerifier(Verifier):
    """A deterministic, dependency-free verifier.

    The verifier compares the claim against evidence excerpts using
    overlap analysis and explicit contradiction markers. It is designed
    to be conservative: related evidence that does not clearly establish
    a claim is classified as INSUFFICIENT_EVIDENCE.

    This makes the verifier usable offline and testable without any
    external model. An LLM-backed verifier can be swapped in behind the
    same interface.
    """

    name = "deterministic"

    # Negation markers that, when applied to a sentence sharing the claim
    # subject, indicate a direct contradiction.
    NEGATION_MARKERS = (
        " is not ",
        " are not ",
        " does not ",
        " do not ",
        " has no ",
        " have no ",
        " never ",
        " is invalid",
        " is revoked",
        " is suspended",
        " lacks ",
        " without ",
        " excludes ",
        " failed to ",
        " is not certified",
        " are not certified",
        " is not valid",
        " is not accredited",
        " are not accredited",
    )

    # "Commitment" words: when the claim asserts one of these but the
    # evidence does not contain any, the evidence is insufficient even if
    # the surrounding text overlaps. This is what lets the verifier tell
    # the difference between "is certified" and "follows ... principles".
    COMMITMENT_WORDS = (
        "certified",
        "certification",
        "certificate",
        "accredited",
        "accreditation",
        "approval",
        "approved",
        "holds certification",
        "has been certified",
    )

    def __init__(
        self,
        min_overlap: float = 0.34,
        strong_overlap: float = 0.6,
        min_tokens: int = 3,
    ) -> None:
        self.min_overlap = min_overlap
        self.strong_overlap = strong_overlap
        self.min_tokens = min_tokens

    def verify(self, claim: Claim, evidence: Sequence[Evidence]) -> VerificationResult:
        if not evidence:
            return VerificationResult(
                claim_id=claim.claim_id,
                status=VerificationStatus.INSUFFICIENT_EVIDENCE,
                explanation=(
                    "No evidence was provided to support or contradict the claim. "
                    "The claim cannot be verified."
                ),
                evidence_ids=[],
                deterministic_confidence=0.0,
                verification_method=VerificationMethod.DETERMINISTIC,
            )

        claim_tokens = _tokenize(claim.text)
        if len(claim_tokens) < self.min_tokens:
            return VerificationResult(
                claim_id=claim.claim_id,
                status=VerificationStatus.INSUFFICIENT_EVIDENCE,
                explanation=(
                    "Claim text is too short to verify reliably against evidence."
                ),
                evidence_ids=[],
                deterministic_confidence=0.0,
                verification_method=VerificationMethod.DETERMINISTIC,
            )

        supporting: list = []
        conflicting: list = []
        max_overlap = 0.0

        for item in evidence:
            overlap = self._overlap_ratio(claim_tokens, item.excerpt)
            if overlap > max_overlap:
                max_overlap = overlap
            relation = self._classify(claim, claim_tokens, item)
            if relation == "support":
                supporting.append(item.evidence_id)
            elif relation == "conflict":
                conflicting.append(item.evidence_id)

        if conflicting:
            return VerificationResult(
                claim_id=claim.claim_id,
                status=VerificationStatus.CONTRADICTED,
                explanation=(
                    "Evidence directly conflicts with the claim. "
                    f"Conflicting evidence IDs: {', '.join(conflicting)}."
                ),
                evidence_ids=conflicting,
                deterministic_confidence=max_overlap,
                verification_method=VerificationMethod.DETERMINISTIC,
            )
        if supporting:
            return VerificationResult(
                claim_id=claim.claim_id,
                status=VerificationStatus.SUPPORTED,
                explanation=(
                    "Evidence supports the claim. "
                    f"Supporting evidence IDs: {', '.join(supporting)}."
                ),
                evidence_ids=supporting,
                deterministic_confidence=max_overlap,
                verification_method=VerificationMethod.DETERMINISTIC,
            )
        return VerificationResult(
            claim_id=claim.claim_id,
            status=VerificationStatus.INSUFFICIENT_EVIDENCE,
            explanation=(
                "Evidence is related to the claim but does not establish it "
                "clearly enough. More specific evidence is required."
            ),
            evidence_ids=[],
            deterministic_confidence=max_overlap,
            verification_method=VerificationMethod.DETERMINISTIC,
        )

    def _classify(self, claim: Claim, claim_tokens: set, item: Evidence) -> str:
        excerpt = item.excerpt
        overlap = self._overlap_ratio(claim_tokens, excerpt)

        if self._is_direct_contradiction(claim.text, excerpt):
            return "conflict"

        if self._is_supporting(claim.text, excerpt, overlap):
            return "support"
        return "insufficient"

    def _is_direct_contradiction(self, claim_text: str, excerpt: str) -> bool:
        """Decide whether the evidence directly contradicts the claim.

        A contradiction requires a sentence in the evidence that:
        1. shares the claim subject, and
        2. contains a negation marker, and
        3. addresses the same commitment as the claim (shares a commitment
           word) OR is a near-duplicate of the claim (very high overlap).

        Rule 3 is what prevents false positives such as the claim
        "Company Alpha follows ISO 9001 principles" being contradicted by a
        different sentence "Company Alpha is not ISO 9001 certified".

        If the claim itself already contains a negation marker, then
        evidence containing the same negation agrees with the claim rather
        than contradicting it. This prevents a claim such as "Company Alpha
        is not ISO 9001 certified" from being wrongly flagged as contradicted
        by evidence that states exactly that.
        """
        claim_lower = claim_text.lower()
        excerpt_lower = excerpt.lower()
        claim_subject = self._subject_phrase(claim_lower)
        if not claim_subject:
            return False
        claim_has_negation = any(marker in claim_lower for marker in self.NEGATION_MARKERS)
        for sentence in _split_sentences(excerpt_lower):
            if not self._shares_subject(claim_subject, sentence):
                continue
            has_negation = any(marker in sentence for marker in self.NEGATION_MARKERS)
            if not has_negation:
                continue
            # If the claim is itself a negation, matching negation = support.
            if claim_has_negation:
                continue
            shared_commitment = self._shares_commitment(claim_lower, sentence)
            near_duplicate = self._overlap_ratio(_tokenize(claim_lower), sentence) >= 0.8
            if shared_commitment or near_duplicate:
                return True
        return False

    def _is_supporting(self, claim_text: str, excerpt: str, overlap: float) -> bool:
        claim_lower = claim_text.lower()
        excerpt_lower = excerpt.lower()
        claim_subject = self._subject_phrase(claim_lower)
        if overlap < self.min_overlap:
            return False

        # If the claim asserts a commitment word (e.g. "certified") but the
        # evidence does not contain any commitment word, the evidence is
        # insufficient even with high overlap.
        claim_commitment = self._commitment_words(claim_lower)
        if claim_commitment and not self._commitment_words(excerpt_lower):
            return False

        for sentence in _split_sentences(excerpt_lower):
            if claim_subject and not self._shares_subject(claim_subject, sentence):
                continue
            if overlap >= self.strong_overlap:
                return True
            assertion_markers = (
                " is ",
                " are ",
                " has ",
                " have ",
                " holds ",
                " satisfies ",
                " meets ",
                " provides ",
                " includes ",
            )
            if any(marker in sentence for marker in assertion_markers):
                return True
        return False

    @staticmethod
    def _commitment_words(text: str) -> set:
        """Return the commitment words present in the text.

        Uses substring matching so that "certified" matches the
        "certified" commitment word even when the claim uses
        "certification".
        """
        found = set()
        for word in DeterministicVerifier.COMMITMENT_WORDS:
            if word in text:
                found.add(word)
        return found

    @staticmethod
    def _normalize_commitment(word: str) -> str:
        """Reduce a commitment word to a canonical stem.

        This lets "certified", "certification", and "certify" all map to
        the same commitment so they can be compared across a claim and
        an evidence sentence.
        """
        w = word
        for suffix in ("tion", "sion", "ment", "ance", "ence", "ing", "ed", "ate", "ize"):
            if w.endswith(suffix) and len(w) - len(suffix) >= 4:
                w = w[: -len(suffix)]
                break
        return w

    @classmethod
    def _shares_commitment(cls, claim_text: str, sentence: str) -> bool:
        """True when the claim and sentence address the same commitment.

        Matching is stem-based so that "certified", "certification", and
        "certify" all count as the same commitment.
        """
        claim_words = {cls._normalize_commitment(w) for w in cls._commitment_words(claim_text)}
        sentence_words = {cls._normalize_commitment(w) for w in cls._commitment_words(sentence)}
        if claim_words & sentence_words:
            return True
        for cw in claim_words:
            for sw in sentence_words:
                if cw in sw or sw in cw:
                    return True
        return False

    @staticmethod
    def _subject_phrase(text: str) -> str:
        """Extract the leading subject noun phrase of a claim.

        Uses the text before the first assertion verb as the subject.
        """
        match = re.match(
            r"^(.*?)(?:\b(?:is|are|has|have|holds?|satisfies?|meets?|provides?|includes?|uses?|follows?)\b)",
            text,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        return ""

    def _shares_subject(self, claim_subject: str, sentence: str) -> bool:
        if not claim_subject:
            return True
        subject_tokens = _tokenize(claim_subject)
        sentence_tokens = _tokenize(sentence)
        if not subject_tokens:
            return True
        shared = subject_tokens & sentence_tokens
        threshold = 2 if len(subject_tokens) >= 3 else 1
        return len(shared) >= threshold

    @staticmethod
    def _overlap_ratio(claim_tokens: set, excerpt: str) -> float:
        excerpt_tokens = _tokenize(excerpt)
        if not excerpt_tokens:
            return 0.0
        return len(claim_tokens & excerpt_tokens) / len(claim_tokens)
