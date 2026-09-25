"""Tests for the ProofPilot FastAPI API.

Uses FastAPI's TestClient with temporary SQLite databases. All tests are
deterministic and require no external LLM, network, or API key.
"""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.core.pipeline import ProofPilotPipeline
from app.ingestion.service import IngestionService
from app.provenance.store import ProvenanceStore
from app.reasoning.claim_generator import DeterministicClaimGenerator
from app.retrieval.retriever import KeywordRetriever
from app.verification.verifier import DeterministicVerifier


@pytest.fixture
def client():
    db_path = os.path.join(tempfile.mkdtemp(), "api.db")
    store = ProvenanceStore(db_path)
    pipeline = ProofPilotPipeline(
        ingestion=IngestionService(),
        retriever=KeywordRetriever(),
        claim_generator=DeterministicClaimGenerator(),
        verifier=DeterministicVerifier(),
        store=store,
    )
    app = create_app(db_path=db_path, pipeline=pipeline)
    app.state.store = store
    app.state.pipeline = pipeline
    app.state.ingestion = pipeline.ingestion
    with TestClient(app) as c:
        yield c
    store.close()


@pytest.fixture
def sample_pdf_bytes():
    from data.sample_pdf import SAMPLE_PDF_BYTES
    return SAMPLE_PDF_BYTES


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_schema_loads(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "paths" in schema
    for path in ("/health", "/documents", "/tasks"):
        assert path in schema["paths"]


def test_pdf_upload(client, sample_pdf_bytes):
    response = client.post(
        "/documents",
        files={"file": ("sample.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "sample.pdf"
    assert data["chunk_count"] >= 1
    assert data["status"] == "INDEXED"


def test_invalid_file_type_rejected(client):
    response = client.post(
        "/documents",
        files={"file": ("evil.txt", b"not a pdf", "text/plain")},
    )
    assert response.status_code == 400


def test_empty_file_rejected(client):
    response = client.post(
        "/documents",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 400


def test_get_document(client, sample_pdf_bytes):
    doc_id = _upload_sample(client, sample_pdf_bytes)
    response = client.get(f"/documents/{doc_id}")
    assert response.status_code == 200
    assert response.json()["document_id"] == doc_id


def test_get_missing_document(client):
    response = client.get("/documents/doc_missing")
    assert response.status_code == 404


def _upload_sample(client, sample_pdf_bytes, filename="sample.pdf"):
    response = client.post(
        "/documents",
        files={"file": (filename, sample_pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 200
    return response.json()["document_id"]


def _create_task(client, sample_pdf_bytes):
    doc_id = _upload_sample(client, sample_pdf_bytes)
    response = client.post(
        "/tasks",
        json={"question": "Does Company Alpha have ISO 9001 certification?", "document_ids": [doc_id]},
    )
    assert response.status_code == 200
    task = response.json()
    assert task["status"] == "COMPLETED"
    assert task["document_ids"] == [doc_id]
    return task["task_id"]


def test_missing_document_returns_404(client):
    response = client.post(
        "/tasks",
        json={"question": "anything", "document_ids": ["doc_missing"]},
    )
    assert response.status_code == 404


def test_task_retrieval(client, sample_pdf_bytes):
    task_id = _create_task(client, sample_pdf_bytes)
    response = client.get(f"/tasks/{task_id}")
    assert response.status_code == 200
    assert response.json()["task_id"] == task_id


def test_missing_task_returns_404(client):
    response = client.get("/tasks/task_missing")
    assert response.status_code == 404


def test_task_result_retrieval(client, sample_pdf_bytes):
    task_id = _create_task(client, sample_pdf_bytes)
    response = client.get(f"/tasks/{task_id}/result")
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == task_id
    assert data["status"] == "COMPLETED"
    assert data["summary"]
    assert data["claims"]
    assert data["evidence"]
    assert "steps" in data["provenance"]


def test_verification_results_visible_through_api(client, sample_pdf_bytes):
    task_id = _create_task(client, sample_pdf_bytes)
    response = client.get(f"/tasks/{task_id}/result")
    data = response.json()
    for claim in data["claims"]:
        assert claim["verification_status"] in ("SUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE")


def test_provenance_visible_through_api(client, sample_pdf_bytes):
    task_id = _create_task(client, sample_pdf_bytes)
    response = client.get(f"/tasks/{task_id}/provenance")
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == task_id
    steps = {r["step"] for r in data["records"]}
    for required in (
        "task_received",
        "document_ingested",
        "retrieval_performed",
        "claims_generated",
        "claims_verified",
        "task_completed",
    ):
        assert required in steps


def test_complete_api_flow(client, sample_pdf_bytes):
    # Upload
    r = client.post("/documents", files={"file": ("a.pdf", sample_pdf_bytes, "application/pdf")})
    assert r.status_code == 200
    doc_id = r.json()["document_id"]

    # Task
    r = client.post("/tasks", json={"question": "Does Company Alpha have ISO 9001 certification?", "document_ids": [doc_id]})
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    # Result
    r = client.get(f"/tasks/{task_id}/result")
    assert r.status_code == 200
    result = r.json()
    assert result["status"] == "COMPLETED"
    assert result["claims"]
    assert result["evidence"]
    assert result["provenance"]["document_ids"] == [doc_id]


def test_multiple_documents(client, sample_pdf_bytes):
    r1 = client.post("/documents", files={"file": ("a.pdf", sample_pdf_bytes, "application/pdf")})
    r2 = client.post("/documents", files={"file": ("b.pdf", sample_pdf_bytes, "application/pdf")})
    doc1 = r1.json()["document_id"]
    doc2 = r2.json()["document_id"]

    r = client.post(
        "/tasks",
        json={"question": "Does Company Alpha have ISO 9001 certification?", "document_ids": [doc1, doc2]},
    )
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    r = client.get(f"/tasks/{task_id}/result")
    assert r.status_code == 200
    assert set(r.json()["provenance"]["document_ids"]) == {doc1, doc2}