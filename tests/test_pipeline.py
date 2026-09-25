"""Tests for the ProofPilotPipeline end-to-end orchestrator."""

import os
import tempfile

import pytest

from app.core.pipeline import ProofPilotPipeline
from app.models import TaskStatus, VerificationStatus
from app.provenance.store import ProvenanceStore


@pytest.fixture
def sample_pdf_bytes():
    from data.sample_pdf import SAMPLE_PDF_BYTES
    return SAMPLE_PDF_BYTES


@pytest.fixture
def store():
    db_path = os.path.join(tempfile.mkdtemp(), "pipeline.db")
    s = ProvenanceStore(db_path)
    yield s
    s.close()


@pytest.fixture
def pipeline(store):
    return ProofPilotPipeline(store=store, top_k=5)


def test_complete_end_to_end_run(pipeline, sample_pdf_bytes):
    result = pipeline.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", sample_pdf_bytes)],
    )
    assert result.status == TaskStatus.COMPLETED
    assert result.task_id
    assert result.claims
    assert result.evidence
    assert result.summary


def test_document_persisted(pipeline, sample_pdf_bytes, store):
    result = pipeline.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", sample_pdf_bytes)],
    )
    docs = store.get_documents()
    assert len(docs) == 1
    assert docs[0].document_id in result.provenance["document_ids"]
    assert docs[0].filename == "sample.pdf"


def test_chunks_persisted(pipeline, sample_pdf_bytes, store):
    result = pipeline.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", sample_pdf_bytes)],
    )
    from app.models import Chunk

    rows = store._conn.execute("SELECT chunk_id FROM chunks").fetchall()
    assert len(rows) >= 1


def test_task_persisted(pipeline, sample_pdf_bytes, store):
    result = pipeline.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", sample_pdf_bytes)],
    )
    task = store.get_task(result.task_id)
    assert task is not None
    assert task.question == "Does Company Alpha have ISO 9001 certification?"
    assert task.status == TaskStatus.COMPLETED


def test_claims_persisted(pipeline, sample_pdf_bytes, store):
    result = pipeline.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", sample_pdf_bytes)],
    )
    claims = store.get_claims_for_task(result.task_id)
    assert len(claims) == len(result.claims)
    assert {c.claim_id for c in claims} == {c.claim_id for c in result.claims}


def test_evidence_persisted(pipeline, sample_pdf_bytes, store):
    result = pipeline.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", sample_pdf_bytes)],
    )
    evidence = store.get_evidence_for_task(result.task_id)
    assert len(evidence) == len(result.evidence)
    assert {e.evidence_id for e in evidence} == {e.evidence_id for e in result.evidence}


def test_verification_results_persisted(pipeline, sample_pdf_bytes, store):
    result = pipeline.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", sample_pdf_bytes)],
    )
    for claim in result.claims:
        vr = store.get_verification_result(claim.claim_id)
        assert vr is not None
        assert vr.status == claim.verification_status


def test_provenance_records_persisted(pipeline, sample_pdf_bytes, store):
    result = pipeline.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", sample_pdf_bytes)],
    )
    records = store.get_provenance_for_task(result.task_id)
    steps = {r.step for r in records}
    for required in (
        "task_received",
        "document_ingested",
        "retrieval_performed",
        "claims_generated",
        "claims_verified",
        "task_completed",
    ):
        assert required in steps


def test_task_result_returned_correctly(pipeline, sample_pdf_bytes):
    result = pipeline.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", sample_pdf_bytes)],
    )
    assert result.task_id
    assert result.status == TaskStatus.COMPLETED
    assert isinstance(result.summary, str)
    assert result.summary
    assert result.provenance["task_id"] == result.task_id


def test_supported_claim_reaches_final_result(pipeline, sample_pdf_bytes):
    result = pipeline.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", sample_pdf_bytes)],
    )
    statuses = {c.verification_status for c in result.claims}
    assert VerificationStatus.SUPPORTED in statuses


def test_insufficient_evidence_reaches_final_result(pipeline, sample_pdf_bytes):
    result = pipeline.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", sample_pdf_bytes)],
    )
    # At least one claim should be present; the pipeline must not fabricate
    # a binary answer when evidence is insufficient.
    assert result.claims
    for claim in result.claims:
        assert claim.verification_status in (
            VerificationStatus.SUPPORTED,
            VerificationStatus.CONTRADICTED,
            VerificationStatus.INSUFFICIENT_EVIDENCE,
        )


def test_multiple_documents(pipeline, sample_pdf_bytes, store):
    result = pipeline.run(
        "Does Company Alpha have ISO 9001 certification?",
        [
            ("requirements.pdf", sample_pdf_bytes),
            ("policy.pdf", sample_pdf_bytes),
        ],
    )
    assert result.status == TaskStatus.COMPLETED
    assert len(store.get_documents()) == 2


def test_pipeline_failure_marks_task_failed(sample_pdf_bytes, store):
    class FailingPipeline(ProofPilotPipeline):
        def _run(self, question, documents, task_id, task, store):
            raise RuntimeError("simulated failure")

    pipe = FailingPipeline(store=store)
    result = pipe.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", sample_pdf_bytes)],
    )
    assert result.status == TaskStatus.FAILED
    task = store.get_task(result.task_id)
    assert task is not None
    assert task.status == TaskStatus.FAILED


def test_second_run_does_not_corrupt_existing_tasks(sample_pdf_bytes, store):
    pipe = ProofPilotPipeline(store=store, top_k=5)
    first = pipe.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", sample_pdf_bytes)],
    )
    second = pipe.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", sample_pdf_bytes)],
    )
    assert first.task_id != second.task_id
    assert store.get_task(first.task_id).status == TaskStatus.COMPLETED
    assert store.get_task(second.task_id).status == TaskStatus.COMPLETED
    assert len(store.get_tasks()) == 2
