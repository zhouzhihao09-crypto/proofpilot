"""Tests for HybridVerifier integration with the real pipeline."""

import os
import tempfile

import pytest

from app.core.pipeline import ProofPilotPipeline
from app.llm.fake import FakeLLMProvider
from app.models import TaskStatus, VerificationMethod, VerificationStatus, Claim
from app.provenance.store import ProvenanceStore
from app.verification.hybrid import HybridVerifier
from app.verification.verifier import DeterministicVerifier
from app.reasoning.claim_generator import ClaimGenerator


@pytest.fixture
def store():
    db_path = os.path.join(tempfile.mkdtemp(), "hybrid_pipeline.db")
    s = ProvenanceStore(db_path)
    yield s
    s.close()


@pytest.fixture
def sample_pdf_bytes():
    from data.sample_pdf import SAMPLE_PDF_BYTES
    return SAMPLE_PDF_BYTES


@pytest.fixture
def fake_llm_provider():
    """Create a fake LLM provider for completed vs bidding scenario."""
    provider = FakeLLMProvider(default_status="INSUFFICIENT_EVIDENCE")
    provider.add_rule(
        "completed",
        "INSUFFICIENT_EVIDENCE",
        "Bidding for projects is not the same as completing them.",
        0.85,
    )
    return provider


@pytest.fixture
def hybrid_verifier(fake_llm_provider):
    """HybridVerifier with fake LLM provider."""
    return HybridVerifier(llm_provider=fake_llm_provider)


class FixedClaimGenerator(ClaimGenerator):
    """Claim generator that produces a fixed claim for testing."""
    
    def __init__(self, claim_text: str):
        self.claim_text = claim_text
    
    def generate(self, task, evidence):
        return [
            Claim(
                claim_id="claim_test",
                task_id=task.task_id,
                text=self.claim_text,
                verification_status=VerificationStatus.INSUFFICIENT_EVIDENCE,
            )
        ]


def test_hybrid_pipeline_semantic_verification_used(store, hybrid_verifier, sample_pdf_bytes):
    """End-to-end test: HybridVerifier with fake LLM reaches semantic path."""
    # Use a custom claim generator that produces a claim triggering semantic verification
    claim_generator = FixedClaimGenerator(
        "Company Alpha has completed 50 government projects."
    )
    
    pipeline = ProofPilotPipeline(
        store=store,
        verifier=hybrid_verifier,
        claim_generator=claim_generator,
    )
    
    text_content = (
        "Company Alpha is currently bidding for government projects "
        "and has announced a target of 50 projects."
    )
    
    # Use simple text ingestion
    from app.ingestion.service import IngestionService
    from app.ingestion.extractors import TextExtractor
    
    class SimpleTextExtractor(TextExtractor):
        def extract(self, data: bytes) -> str:
            return data.decode("utf-8")
        def supports(self, mime_type: str) -> bool:
            return True
    
    ingestion = IngestionService(extractor=SimpleTextExtractor())
    pipeline.ingestion = ingestion
    
    result = pipeline.run(
        "Has Company Alpha completed 50 government projects?",
        [("bidding.txt", text_content.encode("utf-8"))],
    )

    assert result.status == TaskStatus.COMPLETED
    assert result.claims

    # Verify at least one claim was verified with HYBRID method
    hybrid_claims = [
        c for c in result.claims
        if c.verification_method == VerificationMethod.HYBRID
    ]
    assert hybrid_claims, f"Expected at least one HYBRID verification, got: {[(c.text, c.verification_method) for c in result.claims]}"

    # Verify semantic_verification_used provenance event was recorded
    records = store.get_provenance_for_task(result.task_id)
    semantic_events = [r for r in records if r.step == "semantic_verification_used"]
    assert semantic_events, "Expected semantic_verification_used provenance event"

    # Verify the provenance event contains expected detail
    event = semantic_events[0]
    assert event.detail.get("deterministic_status") is not None
    assert event.detail.get("semantic_status") is not None
    assert event.detail.get("final_status") is not None
    assert "semantic_confidence" in event.detail


def test_hybrid_pipeline_audit_report_exposes_metadata(store, hybrid_verifier, sample_pdf_bytes):
    """Audit report exposes verification_method and semantic metadata for HYBRID claims."""
    from app.ingestion.service import IngestionService
    from app.ingestion.extractors import TextExtractor
    from app.reasoning.claim_generator import ClaimGenerator
    
    class FixedClaimGenerator(ClaimGenerator):
        def generate(self, task, evidence):
            return [
                Claim(
                    claim_id="claim_test",
                    task_id=task.task_id,
                    text="Company Alpha has completed 50 government projects.",
                    verification_status=VerificationStatus.INSUFFICIENT_EVIDENCE,
                )
            ]
    
    class SimpleTextExtractor(TextExtractor):
        def extract(self, data: bytes) -> str:
            return data.decode("utf-8")
        def supports(self, mime_type: str) -> bool:
            return True
    
    ingestion = IngestionService(extractor=SimpleTextExtractor())
    claim_generator = FixedClaimGenerator()
    
    pipeline = ProofPilotPipeline(
        store=store,
        verifier=hybrid_verifier,
        ingestion=ingestion,
        claim_generator=claim_generator,
    )
    
    text_content = (
        "Company Alpha is currently bidding for government projects "
        "and has announced a target of 50 projects."
    )
    
    result = pipeline.run(
        "Has Company Alpha completed 50 government projects?",
        [("bidding.txt", text_content.encode("utf-8"))],
    )

    from app.audit.report import AuditReportBuilder

    builder = AuditReportBuilder(store)
    report = builder.build(result.task_id)

    assert report.used_semantic_verification is True

    hybrid_claims = [c for c in report.claims if c.verification_method == VerificationMethod.HYBRID]
    assert hybrid_claims, "Expected at least one HYBRID claim in audit report"

    for claim in hybrid_claims:
        assert claim.verification_method == VerificationMethod.HYBRID
        assert claim.deterministic_status is not None
        assert claim.semantic_status is not None
        assert claim.semantic_confidence is not None
        assert claim.semantic_explanation is not None


def test_hybrid_pipeline_markdown_render_includes_semantic(store, hybrid_verifier, sample_pdf_bytes):
    """Markdown report includes semantic verification details."""
    from app.ingestion.service import IngestionService
    from app.ingestion.extractors import TextExtractor
    from app.reasoning.claim_generator import ClaimGenerator
    
    class FixedClaimGenerator(ClaimGenerator):
        def generate(self, task, evidence):
            return [
                Claim(
                    claim_id="claim_test",
                    task_id=task.task_id,
                    text="Company Alpha has completed 50 government projects.",
                    verification_status=VerificationStatus.INSUFFICIENT_EVIDENCE,
                )
            ]
    
    class SimpleTextExtractor(TextExtractor):
        def extract(self, data: bytes) -> str:
            return data.decode("utf-8")
        def supports(self, mime_type: str) -> bool:
            return True
    
    ingestion = IngestionService(extractor=SimpleTextExtractor())
    claim_generator = FixedClaimGenerator()
    
    pipeline = ProofPilotPipeline(
        store=store,
        verifier=hybrid_verifier,
        ingestion=ingestion,
        claim_generator=claim_generator,
    )
    
    text_content = (
        "Company Alpha is currently bidding for government projects "
        "and has announced a target of 50 projects."
    )
    
    result = pipeline.run(
        "Has Company Alpha completed 50 government projects?",
        [("bidding.txt", text_content.encode("utf-8"))],
    )

    from app.audit.markdown import render_markdown
    from app.audit.report import AuditReportBuilder

    builder = AuditReportBuilder(store)
    report = builder.build(result.task_id)
    markdown = render_markdown(report)

    assert "Semantic verification: used" in markdown
    assert "Verification method: HYBRID" in markdown
    assert "Deterministic result:" in markdown
    assert "Semantic result:" in markdown
    assert "Semantic confidence:" in markdown
    assert "Semantic explanation:" in markdown


def test_hybrid_pipeline_deterministic_contradiction_guardrail(hybrid_verifier, store, sample_pdf_bytes):
    """Deterministic CONTRADICTED remains CONTRADICTED even with LLM available."""
    pipeline = ProofPilotPipeline(store=store, verifier=hybrid_verifier)
    result = pipeline.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", sample_pdf_bytes)],
    )

    contradicted_claims = [
        c for c in result.claims
        if c.verification_status == VerificationStatus.CONTRADICTED
    ]
    assert contradicted_claims, "Expected at least one CONTRADICTED claim"

    for claim in contradicted_claims:
        # Contradiction is a guardrail - should be DETERMINISTIC, not HYBRID
        assert claim.verification_method == VerificationMethod.DETERMINISTIC


def test_default_pipeline_uses_deterministic_verifier(store, sample_pdf_bytes):
    """Default pipeline (no explicit verifier) uses DeterministicVerifier."""
    pipeline = ProofPilotPipeline(store=store)
    result = pipeline.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", sample_pdf_bytes)],
    )

    assert result.status == TaskStatus.COMPLETED

    # All claims should have DETERMINISTIC verification method
    for claim in result.claims:
        assert claim.verification_method == VerificationMethod.DETERMINISTIC

    # No semantic verification provenance
    records = store.get_provenance_for_task(result.task_id)
    semantic_events = [r for r in records if r.step == "semantic_verification_used"]
    assert not semantic_events, "Default pipeline should not use semantic verification"


def test_explicit_hybrid_verifier_takes_precedence_over_mode(store, hybrid_verifier, sample_pdf_bytes):
    """Explicitly injected HybridVerifier works regardless of configuration."""
    from app.ingestion.service import IngestionService
    from app.ingestion.extractors import TextExtractor
    from app.reasoning.claim_generator import ClaimGenerator
    
    class FixedClaimGenerator(ClaimGenerator):
        def generate(self, task, evidence):
            return [
                Claim(
                    claim_id="claim_test",
                    task_id=task.task_id,
                    text="Company Alpha has completed 50 government projects.",
                    verification_status=VerificationStatus.INSUFFICIENT_EVIDENCE,
                )
            ]
    
    class SimpleTextExtractor(TextExtractor):
        def extract(self, data: bytes) -> str:
            return data.decode("utf-8")
        def supports(self, mime_type: str) -> bool:
            return True
    
    ingestion = IngestionService(extractor=SimpleTextExtractor())
    claim_generator = FixedClaimGenerator()
    
    pipeline = ProofPilotPipeline(
        store=store,
        verifier=hybrid_verifier,
        ingestion=ingestion,
        claim_generator=claim_generator,
    )
    
    text_content = (
        "Company Alpha is currently bidding for government projects "
        "and has announced a target of 50 projects."
    )
    
    result = pipeline.run(
        "Has Company Alpha completed 50 government projects?",
        [("bidding.txt", text_content.encode("utf-8"))],
    )

    assert result.status == TaskStatus.COMPLETED
    hybrid_claims = [c for c in result.claims if c.verification_method == VerificationMethod.HYBRID]
    assert hybrid_claims, "Explicit HybridVerifier should be used"
