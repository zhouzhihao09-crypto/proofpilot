"""Tests for API verifier mode configuration."""

import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.core.pipeline import ProofPilotPipeline
from app.ingestion.service import IngestionService
from app.ingestion.extractors import TextExtractor
from app.llm.fake import FakeLLMProvider
from app.models import TaskStatus, VerificationMethod, VerificationStatus, Claim
from app.provenance.store import ProvenanceStore
from app.reasoning.claim_generator import ClaimGenerator
from app.verification.hybrid import HybridVerifier
from app.verification.verifier import DeterministicVerifier


@pytest.fixture
def sample_pdf_bytes():
    from data.sample_pdf import SAMPLE_PDF_BYTES
    return SAMPLE_PDF_BYTES


@pytest.fixture
def store():
    db_path = os.path.join(tempfile.mkdtemp(), "api_test.db")
    s = ProvenanceStore(db_path)
    yield s
    s.close()


@pytest.fixture
def client():
    """Default app client (deterministic mode)."""
    with patch.dict(os.environ, {"VERIFIER_MODE": "deterministic"}):
        app = create_app()
        return TestClient(app)


class FixedClaimGenerator(ClaimGenerator):
    """Claim generator that produces a fixed claim for testing hybrid verification."""
    
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


def test_default_api_uses_deterministic_verifier(client, sample_pdf_bytes):
    """Default API mode is deterministic."""
    # Upload a document
    response = client.post(
        "/documents",
        files={"file": ("sample.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 200
    document_id = response.json()["document_id"]

    # Create a task
    response = client.post(
        "/tasks",
        json={"question": "Does Company Alpha have ISO 9001 certification?", "document_ids": [document_id]},
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]

    # Get result
    response = client.get(f"/tasks/{task_id}/result")
    assert response.status_code == 200
    result = response.json()

    # All claims should have DETERMINISTIC verification method
    for claim in result["claims"]:
        assert claim["verification_method"] == "DETERMINISTIC"


def test_api_verifier_mode_env_var_deterministic(sample_pdf_bytes):
    """VERIFIER_MODE=deterministic env var selects deterministic verifier."""
    with patch.dict(os.environ, {"VERIFIER_MODE": "deterministic"}):
        app = create_app()
        client = TestClient(app)

        response = client.post(
            "/documents",
            files={"file": ("sample.pdf", sample_pdf_bytes, "application/pdf")},
        )
        document_id = response.json()["document_id"]

        response = client.post(
            "/tasks",
            json={"question": "Does Company Alpha have ISO 9001 certification?", "document_ids": [document_id]},
        )
        task_id = response.json()["task_id"]

        response = client.get(f"/tasks/{task_id}/result")
        result = response.json()

        for claim in result["claims"]:
            assert claim["verification_method"] == "DETERMINISTIC"


def test_api_explicit_pipeline_takes_precedence_over_env_var(sample_pdf_bytes, store):
    """Explicit pipeline injection takes precedence over VERIFIER_MODE."""
    # Create a custom pipeline with HybridVerifier + fake LLM
    # Use a claim generator that produces a claim triggering semantic verification
    fake_provider = FakeLLMProvider()
    fake_provider.add_rule("completed", "SUPPORTED", "Semantic support.", 0.92)

    claim_generator = FixedClaimGenerator()
    
    pipeline = ProofPilotPipeline(
        store=store,
        verifier=HybridVerifier(llm_provider=fake_provider),
        claim_generator=claim_generator,
    )

    # Even with VERIFIER_MODE=deterministic, explicit pipeline should win
    with patch.dict(os.environ, {"VERIFIER_MODE": "deterministic"}):
        app = create_app(pipeline=pipeline)
        client = TestClient(app)

        # Use sample PDF for document upload (API only accepts PDF)
        response = client.post(
            "/documents",
            files={"file": ("sample.pdf", sample_pdf_bytes, "application/pdf")},
        )
        document_id = response.json()["document_id"]

        # Task with a question that will trigger the fixed claim
        response = client.post(
            "/tasks",
            json={"question": "Has Company Alpha completed 50 government projects?", "document_ids": [document_id]},
        )
        task_id = response.json()["task_id"]

        response = client.get(f"/tasks/{task_id}/result")
        result = response.json()

        # Should have HYBRID claims because explicit pipeline was injected
        hybrid_claims = [c for c in result["claims"] if c["verification_method"] == "HYBRID"]
        assert hybrid_claims, "Explicit pipeline should take precedence over env var"


def test_api_invalid_verifier_mode_fails():
    """Invalid VERIFIER_MODE raises clear error."""
    with patch.dict(os.environ, {"VERIFIER_MODE": "invalid_mode"}):
        with pytest.raises(ValueError, match="Invalid VERIFIER_MODE"):
            create_app()


def test_api_hybrid_mode_without_llm_provider_fails():
    """VERIFIER_MODE=hybrid without LLM provider fails clearly."""
    with patch.dict(os.environ, {"VERIFIER_MODE": "hybrid"}):
        with pytest.raises(ValueError, match="Hybrid verifier mode requires an explicit LLMProvider"):
            create_app()


def test_api_hybrid_mode_with_custom_pipeline_works(sample_pdf_bytes, store):
    """Hybrid mode works when custom pipeline with LLM provider is injected."""
    fake_provider = FakeLLMProvider()
    fake_provider.add_rule("completed", "SUPPORTED", "Semantic support.", 0.92)

    claim_generator = FixedClaimGenerator()
    
    pipeline = ProofPilotPipeline(
        store=store,
        verifier=HybridVerifier(llm_provider=fake_provider),
        claim_generator=claim_generator,
    )

    with patch.dict(os.environ, {"VERIFIER_MODE": "hybrid"}):
        app = create_app(pipeline=pipeline)
        client = TestClient(app)

        # Use sample PDF for document upload (API only accepts PDF)
        response = client.post(
            "/documents",
            files={"file": ("sample.pdf", sample_pdf_bytes, "application/pdf")},
        )
        document_id = response.json()["document_id"]

        response = client.post(
            "/tasks",
            json={"question": "Has Company Alpha completed 50 government projects?", "document_ids": [document_id]},
        )
        task_id = response.json()["task_id"]

        response = client.get(f"/tasks/{task_id}/result")
        result = response.json()

        hybrid_claims = [c for c in result["claims"] if c["verification_method"] == "HYBRID"]
        assert hybrid_claims, "Hybrid mode should work with injected pipeline"


def test_api_verifier_mode_parameter_takes_precedence(sample_pdf_bytes, store):
    """Explicit verifier_mode parameter takes precedence over env var."""
    fake_provider = FakeLLMProvider()
    fake_provider.add_rule("completed", "SUPPORTED", "Semantic support.", 0.92)

    claim_generator = FixedClaimGenerator()
    
    pipeline = ProofPilotPipeline(
        store=store,
        verifier=HybridVerifier(llm_provider=fake_provider),
        claim_generator=claim_generator,
    )

    # Env var says deterministic, but parameter says hybrid with explicit pipeline
    with patch.dict(os.environ, {"VERIFIER_MODE": "deterministic"}):
        app = create_app(pipeline=pipeline, verifier_mode="hybrid")
        client = TestClient(app)

        # Use sample PDF for document upload (API only accepts PDF)
        response = client.post(
            "/documents",
            files={"file": ("sample.pdf", sample_pdf_bytes, "application/pdf")},
        )
        document_id = response.json()["document_id"]

        response = client.post(
            "/tasks",
            json={"question": "Has Company Alpha completed 50 government projects?", "document_ids": [document_id]},
        )
        task_id = response.json()["task_id"]

        response = client.get(f"/tasks/{task_id}/result")
        result = response.json()

        hybrid_claims = [c for c in result["claims"] if c["verification_method"] == "HYBRID"]
        assert hybrid_claims, "Explicit verifier_mode parameter should take precedence"
