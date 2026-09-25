"""Tests for the audit report builder, Markdown renderer, and report endpoint."""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.audit.markdown import render_markdown
from app.audit.report import AuditReportBuilder
from app.core.pipeline import ProofPilotPipeline
from app.ingestion.service import IngestionService
from app.models import VerificationStatus
from app.provenance.store import ProvenanceStore
from app.retrieval.retriever import KeywordRetriever
from app.reasoning.claim_generator import DeterministicClaimGenerator
from app.verification.verifier import DeterministicVerifier


@pytest.fixture
def store():
    db_path = os.path.join(tempfile.mkdtemp(), "audit.db")
    s = ProvenanceStore(db_path)
    yield s
    s.close()


@pytest.fixture
def pipeline(store):
    return ProofPilotPipeline(
        ingestion=IngestionService(),
        retriever=KeywordRetriever(),
        claim_generator=DeterministicClaimGenerator(),
        verifier=DeterministicVerifier(),
        store=store,
    )


@pytest.fixture
def completed_task(pipeline, store):
    from data.sample_pdf import SAMPLE_PDF_BYTES

    result = pipeline.run(
        "Does Company Alpha have ISO 9001 certification?",
        [("sample.pdf", SAMPLE_PDF_BYTES)],
    )
    return result.task_id
def test_report_generated_for_completed_task(store, completed_task):
    builder = AuditReportBuilder(store)
    report = builder.build(completed_task)
    assert report.task_id == completed_task
    assert report.status == "COMPLETED"
    assert report.claims
    assert report.evidence


def test_task_metadata_included(store, completed_task):
    builder = AuditReportBuilder(store)
    report = builder.build(completed_task)
    assert report.question
    assert report.created_at is not None
    assert report.updated_at is not None
    assert report.document_ids


def test_claims_included(store, completed_task):
    builder = AuditReportBuilder(store)
    report = builder.build(completed_task)
    for claim in report.claims:
        assert claim.claim_id
        assert claim.text
        assert claim.verification_status in (
            VerificationStatus.SUPPORTED,
            VerificationStatus.CONTRADICTED,
            VerificationStatus.INSUFFICIENT_EVIDENCE,
        )


def test_verification_statuses_included(store, completed_task):
    builder = AuditReportBuilder(store)
    report = builder.build(completed_task)
    for claim in report.claims:
        assert claim.verification_status is not None
def test_supporting_evidence_included(store, completed_task):
    builder = AuditReportBuilder(store)
    report = builder.build(completed_task)
    supported_claims = [c for c in report.claims if c.verification_status == VerificationStatus.SUPPORTED]
    if supported_claims:
        claim = supported_claims[0]
        assert claim.supporting
        for evidence in claim.supporting:
            assert evidence.evidence_id in claim.supporting_evidence_ids


def test_conflicting_evidence_included(store, completed_task):
    builder = AuditReportBuilder(store)
    report = builder.build(completed_task)
    contradicted_claims = [c for c in report.claims if c.verification_status == VerificationStatus.CONTRADICTED]
    if contradicted_claims:
        claim = contradicted_claims[0]
        assert claim.conflicting
        for evidence in claim.conflicting:
            assert evidence.evidence_id in claim.conflicting_evidence_ids


def test_evidence_page_numbers_included(store, completed_task):
    builder = AuditReportBuilder(store)
    report = builder.build(completed_task)
    for evidence in report.evidence:
        assert evidence.evidence_id
        assert evidence.document_id
        assert evidence.excerpt


def test_evidence_excerpts_included(store, completed_task):
    builder = AuditReportBuilder(store)
    report = builder.build(completed_task)
    for evidence in report.evidence:
        assert evidence.excerpt
        assert len(evidence.excerpt) > 0


def test_provenance_included(store, completed_task):
    builder = AuditReportBuilder(store)
    report = builder.build(completed_task)
    assert report.provenance
    steps = {r.step for r in report.provenance}
    for required in ("task_received", "document_ingested", "retrieval_performed", "claims_generated", "claims_verified", "task_completed"):
        assert required in steps
def test_security_events_included(store, completed_task):
    builder = AuditReportBuilder(store)
    report = builder.build(completed_task)
    # Security events may or may not be present depending on the task;
    # the report must expose the field either way.
    assert hasattr(report, "security_events")
    for event in report.security_events:
        assert event.step in ("contradiction_detected", "insufficient_evidence")


def test_evidence_graph_relationships_correct(store, completed_task):
    builder = AuditReportBuilder(store)
    report = builder.build(completed_task)
    evidence_by_id = {e.evidence_id: e for e in report.evidence}
    for claim in report.claims:
        for eid in claim.supporting_evidence_ids:
            assert eid in evidence_by_id
        for eid in claim.conflicting_evidence_ids:
            assert eid in evidence_by_id
        # Supporting nodes must match the supporting IDs.
        assert {e.evidence_id for e in claim.supporting} == set(claim.supporting_evidence_ids)
        assert {e.evidence_id for e in claim.conflicting} == set(claim.conflicting_evidence_ids)


def test_missing_task_raises(store):
    builder = AuditReportBuilder(store)
    with pytest.raises(KeyError):
        builder.build("task_missing")


def test_report_endpoint_works_through_api(store, completed_task):
    from app.api.app import create_app

    pipeline = ProofPilotPipeline(
        ingestion=IngestionService(),
        retriever=KeywordRetriever(),
        claim_generator=DeterministicClaimGenerator(),
        verifier=DeterministicVerifier(),
        store=store,
    )
    app = create_app(db_path=store.db_path, pipeline=pipeline)
    app.state.store = store
    app.state.pipeline = pipeline
    app.state.ingestion = pipeline.ingestion
    with TestClient(app) as client:
        response = client.get(f"/tasks/{completed_task}/report")
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == completed_task
        assert data["claims"]
        assert data["evidence"]


def test_report_endpoint_missing_task_404(store):
    from app.api.app import create_app

    app = create_app(db_path=store.db_path)
    app.state.store = store
    app.state.pipeline = None
    app.state.ingestion = None
    with TestClient(app) as client:
        response = client.get("/tasks/task_missing/report")
        assert response.status_code == 404


def test_report_does_not_rerun_pipeline(store, completed_task):
    """Building the report must not create new tasks or claims."""
    task_count_before = len(store.get_tasks())
    claim_count_before = len(store.get_claims_for_task(completed_task))
    builder = AuditReportBuilder(store)
    builder.build(completed_task)
    assert len(store.get_tasks()) == task_count_before
    assert len(store.get_claims_for_task(completed_task)) == claim_count_before


def test_markdown_render_contains_required_sections(store, completed_task):
    builder = AuditReportBuilder(store)
    report = builder.build(completed_task)
    markdown = render_markdown(report)
    for section in ("# ProofPilot Audit Report", "## Task", "## Verification Summary", "## Claims", "## Evidence", "## Provenance", "## Security Events", "## Limitations"):
        assert section in markdown
    assert completed_task in markdown


def test_markdown_render_is_deterministic(store, completed_task):
    builder = AuditReportBuilder(store)
    report = builder.build(completed_task)
    first = render_markdown(report)
    second = render_markdown(report)
    assert first == second
