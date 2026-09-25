"""Tests for the verification module.

These tests are deterministic and do not require any external LLM or
network access.
"""

import pytest

from app.models import Claim, Evidence, VerificationStatus
from app.verification.verifier import DeterministicVerifier


@pytest.fixture
def verifier():
    return DeterministicVerifier()


@pytest.fixture
def task_id():
    return "task_test"


def _claim(text="Company Alpha has ISO 9001 certification", task_id="task_test"):
    return Claim(claim_id="claim_1", task_id=task_id, text=text)


def _evidence(eid, excerpt, document_id="doc_1"):
    return Evidence(evidence_id=eid, document_id=document_id, excerpt=excerpt)


def test_supported_claim(verifier):
    claim = _claim()
    evidence = [_evidence("ev1", "Company Alpha has ISO 9001 certification")]
    result = verifier.verify(claim, evidence)
    assert result.status == VerificationStatus.SUPPORTED
    assert result.evidence_ids == ["ev1"]
    assert result.explanation  # non-empty


def test_supported_with_multiple_evidence(verifier):
    claim = _claim()
    evidence = [
        _evidence("ev1", "Company Alpha holds ISO 9001 certification"),
        _evidence("ev2", "Company Alpha has ISO 9001 certification"),
    ]
    result = verifier.verify(claim, evidence)
    assert result.status == VerificationStatus.SUPPORTED
    assert set(result.evidence_ids) == {"ev1", "ev2"}


def test_insufficient_evidence_principles_not_certification(verifier):
    # The key example from the specification: following ISO 9001 principles
    # does not establish that the company is certified.
    claim = _claim(text="Company Alpha is ISO 9001 certified")
    evidence = [_evidence("ev1", "Company Alpha follows ISO 9001 principles")]
    result = verifier.verify(claim, evidence)
    assert result.status == VerificationStatus.INSUFFICIENT_EVIDENCE


def test_insufficient_evidence_when_no_commitment_word(verifier):
    claim = _claim(text="Supplier X is ISO 9001 certified")
    evidence = [_evidence("ev1", "Supplier X follows ISO 9001 principles")]
    result = verifier.verify(claim, evidence)
    assert result.status == VerificationStatus.INSUFFICIENT_EVIDENCE


def test_insufficient_evidence_unrelated_text(verifier):
    claim = _claim()
    evidence = [_evidence("ev1", "The weather today is pleasant")]
    result = verifier.verify(claim, evidence)
    assert result.status == VerificationStatus.INSUFFICIENT_EVIDENCE


def test_insufficient_evidence_empty(verifier):
    claim = _claim()
    result = verifier.verify(claim, [])
    assert result.status == VerificationStatus.INSUFFICIENT_EVIDENCE
    assert result.evidence_ids == []


def test_insufficient_evidence_short_claim(verifier):
    claim = _claim(text="Alpha")
    evidence = [_evidence("ev1", "Alpha has ISO 9001 certification")]
    result = verifier.verify(claim, evidence)
    assert result.status == VerificationStatus.INSUFFICIENT_EVIDENCE


def test_contradicted_claim(verifier):
    claim = _claim()
    evidence = [_evidence("ev1", "Company Alpha is not ISO 9001 certified")]
    result = verifier.verify(claim, evidence)
    assert result.status == VerificationStatus.CONTRADICTED
    assert result.evidence_ids == ["ev1"]


def test_contradicted_takes_precedence_over_support(verifier):
    claim = _claim()
    evidence = [
        _evidence("ev1", "Company Alpha holds ISO 9001 certification"),
        _evidence("ev2", "Company Alpha is not ISO 9001 certified"),
    ]
    result = verifier.verify(claim, evidence)
    assert result.status == VerificationStatus.CONTRADICTED
    assert result.evidence_ids == ["ev2"]


def test_evidence_ids_preserved(verifier):
    claim = _claim()
    evidence = [
        _evidence("unique-id-123", "Company Alpha has ISO 9001 certification"),
    ]
    result = verifier.verify(claim, evidence)
    assert result.evidence_ids == ["unique-id-123"]


def test_result_has_claim_id(verifier):
    claim = _claim()
    claim.claim_id = "claim_xyz"
    evidence = [_evidence("ev1", "Company Alpha has ISO 9001 certification")]
    result = verifier.verify(claim, evidence)
    assert result.claim_id == "claim_xyz"


def test_only_conflicting_evidence_returned(verifier):
    claim = _claim()
    evidence = [
        _evidence("ev1", "unrelated text about something else"),
        _evidence("ev2", "Company Alpha is not ISO 9001 certified"),
    ]
    result = verifier.verify(claim, evidence)
    assert result.status == VerificationStatus.CONTRADICTED
    assert result.evidence_ids == ["ev2"]


def test_supported_certification_word_in_evidence(verifier):
    claim = _claim(text="Supplier X is ISO 9001 certified")
    evidence = [_evidence("ev1", "Supplier X is ISO 9001 certified")]
    result = verifier.verify(claim, evidence)
    assert result.status == VerificationStatus.SUPPORTED
