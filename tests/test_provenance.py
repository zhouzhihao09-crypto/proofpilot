"""Tests for the ProvenanceStore (SQLite persistence)."""

import os
import sqlite3
import tempfile

import pytest

from app.models import (
    Claim,
    Document,
    DocumentStatus,
    Chunk,
    Evidence,
    ProvenanceRecord,
    Task,
    TaskStatus,
    VerificationResult,
    VerificationStatus,
)
from app.provenance.store import ProvenanceStore


@pytest.fixture
def store():
    db_dir = tempfile.mkdtemp()
    db_path = os.path.join(db_dir, "test.db")
    s = ProvenanceStore(db_path)
    yield s
    s.close()


def _task(task_id="task_1", question="Is Supplier X certified?", document_ids=None):
    return Task(
        task_id=task_id,
        question=question,
        document_ids=document_ids if document_ids is not None else ["doc_1"],
        status=TaskStatus.COMPLETED,
    )


def _document(document_id="doc_1", filename="requirements.pdf"):
    return Document(
        document_id=document_id,
        filename=filename,
        mime_type="application/pdf",
        page_count=3,
        status=DocumentStatus.INDEXED,
        metadata={"source": "upload", "pages": "3"},
    )


def _chunk(chunk_id="chunk_1", document_id="doc_1", text="Supplier X is certified."):
    return Chunk(
        chunk_id=chunk_id,
        document_id=document_id,
        page=1,
        text=text,
        start_char=0,
        end_char=len(text),
        sequence=0,
        token_estimate=4,
    )


def _evidence(evidence_id="ev_1", document_id="doc_1", chunk_id="chunk_1", claim_id="claim_1", excerpt="Supplier X is certified."):
    return Evidence(
        evidence_id=evidence_id,
        document_id=document_id,
        chunk_id=chunk_id,
        claim_id=claim_id,
        source="requirements.pdf",
        page=1,
        excerpt=excerpt,
        retrieval_score=0.9,
    )


def _claim(claim_id="claim_1", task_id="task_1", text="Supplier X is certified."):
    return Claim(
        claim_id=claim_id,
        task_id=task_id,
        text=text,
        verification_status=VerificationStatus.SUPPORTED,
        supporting_evidence_ids=["ev_1"],
        conflicting_evidence_ids=[],
        explanation="Supported by evidence.",
    )


def _verification(claim_id="claim_1"):
    return VerificationResult(
        claim_id=claim_id,
        status=VerificationStatus.SUPPORTED,
        explanation="Supported by evidence.",
        evidence_ids=["ev_1"],
    )


def _provenance(record_id="rec_1", task_id="task_1", step="extract"):
    return ProvenanceRecord(
        record_id=record_id,
        task_id=task_id,
        step=step,
        actor="system",
        input_refs=["doc_1"],
        output_refs=["claim_1"],
        detail={"pages": 3, "chunks": 5},
    )

def test_database_initialization(store):
    tables = {
        row[0]
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    for required in (
        "documents",
        "tasks",
        "chunks",
        "evidence",
        "claims",
        "verification_results",
        "provenance",
    ):
        assert required in tables


def test_foreign_keys_enabled(store):
    pragma = store._conn.execute("PRAGMA foreign_keys").fetchone()
    assert pragma[0] == 1


def test_save_and_get_task(store):
    task = _task()
    store.save_task(task)
    loaded = store.get_task("task_1")
    assert loaded is not None
    assert loaded.question == "Is Supplier X certified?"
    assert loaded.status == TaskStatus.COMPLETED
    assert loaded.document_ids == ["doc_1"]


def test_save_and_get_document(store):
    doc = _document()
    store.save_document(doc)
    loaded = store.get_document("doc_1")
    assert loaded is not None
    assert loaded.filename == "requirements.pdf"
    assert loaded.page_count == 3
    assert loaded.status == DocumentStatus.INDEXED
    assert loaded.metadata == {"source": "upload", "pages": "3"}


def test_save_and_get_evidence(store):
    store.save_task(_task())
    store.save_document(_document())
    store.save_chunk(_chunk())
    store.save_claim(_claim())
    ev = _evidence()
    store.save_evidence(ev)
    loaded = store.get_evidence("ev_1")
    assert loaded is not None
    assert loaded.excerpt == "Supplier X is certified."
    assert loaded.retrieval_score == 0.9
    assert loaded.claim_id == "claim_1"
    assert loaded.chunk_id == "chunk_1"
    assert loaded.document_id == "doc_1"


def test_save_and_get_claim(store):
    store.save_task(_task())
    claim = _claim()
    store.save_claim(claim)
    loaded = store.get_claim("claim_1")
    assert loaded is not None
    assert loaded.text == "Supplier X is certified."
    assert loaded.verification_status == VerificationStatus.SUPPORTED
    assert loaded.supporting_evidence_ids == ["ev_1"]


def test_save_and_get_verification_result(store):
    vr = _verification()
    store.save_verification_result(vr)
    loaded = store.get_verification_result("claim_1")
    assert loaded is not None
    assert loaded.status == VerificationStatus.SUPPORTED
    assert loaded.evidence_ids == ["ev_1"]


def test_save_and_get_provenance(store):
    store.save_task(_task())
    record = _provenance()
    store.save_provenance(record)
    loaded = store.get_provenance_for_task("task_1")
    assert len(loaded) == 1
    assert loaded[0].step == "extract"
    assert loaded[0].actor == "system"
    assert loaded[0].input_refs == ["doc_1"]
    assert loaded[0].output_refs == ["claim_1"]
    assert loaded[0].detail == {"pages": 3, "chunks": 5}

def test_evidence_linked_to_claim(store):
    store.save_task(_task())
    store.save_document(_document())
    store.save_chunk(_chunk())
    store.save_claim(_claim())
    store.save_evidence(_evidence())
    loaded = store.get_evidence("ev_1")
    assert loaded.claim_id == "claim_1"


def test_claims_retrieved_by_task(store):
    store.save_task(_task())
    store.save_claim(_claim(claim_id="claim_1"))
    store.save_claim(_claim(claim_id="claim_2", text="Other claim."))
    claims = store.get_claims_for_task("task_1")
    assert len(claims) == 2
    assert {c.claim_id for c in claims} == {"claim_1", "claim_2"}


def test_evidence_retrieved_by_task(store):
    store.save_task(_task())
    store.save_document(_document())
    store.save_chunk(_chunk())
    store.save_claim(_claim())
    store.save_evidence(_evidence())
    found = store.get_evidence_for_task("task_1")
    assert len(found) == 1
    assert found[0].evidence_id == "ev_1"


def test_provenance_retrieved_by_task(store):
    store.save_task(_task())
    store.save_provenance(_provenance(record_id="rec_1"))
    store.save_provenance(_provenance(record_id="rec_2", step="verify"))
    records = store.get_provenance_for_task("task_1")
    assert len(records) == 2
    assert {r.record_id for r in records} == {"rec_1", "rec_2"}


def test_json_reference_fields_round_trip(store):
    store.save_task(_task())
    claim = _claim()
    claim.supporting_evidence_ids = ["ev_a", "ev_b", "ev_c"]
    claim.conflicting_evidence_ids = ["ev_x"]
    store.save_claim(claim)
    loaded = store.get_claim("claim_1")
    assert loaded.supporting_evidence_ids == ["ev_a", "ev_b", "ev_c"]
    assert loaded.conflicting_evidence_ids == ["ev_x"]


def test_metadata_round_trip(store):
    store.save_document(_document())
    loaded = store.get_document("doc_1")
    assert loaded.metadata == {"source": "upload", "pages": "3"}


def test_multiple_records_for_same_task(store):
    store.save_task(_task(document_ids=["doc_1", "doc_2"]))
    store.save_document(_document(document_id="doc_1"))
    store.save_document(_document(document_id="doc_2", filename="policy.pdf"))
    store.save_claim(_claim(claim_id="claim_1"))
    store.save_claim(_claim(claim_id="claim_2", text="Another claim."))
    store.save_chunk(_chunk(chunk_id="chunk_1"))
    store.save_chunk(_chunk(chunk_id="chunk_2", text="Another excerpt."))
    store.save_evidence(_evidence(evidence_id="ev_1"))
    store.save_evidence(_evidence(evidence_id="ev_2", chunk_id="chunk_2", excerpt="Another excerpt."))
    store.save_verification_result(_verification(claim_id="claim_1"))
    store.save_verification_result(_verification(claim_id="claim_2"))
    store.save_provenance(_provenance(record_id="rec_1"))
    store.save_provenance(_provenance(record_id="rec_2", step="verify"))

    full = store.get_task_full("task_1")
    assert full["task"].task_id == "task_1"
    assert len(full["documents"]) == 2
    assert len(full["claims"]) == 2
    assert len(full["evidence"]) == 2
    assert len(full["provenance"]) == 2


def test_upsert_task_updates_fields(store):
    store.save_task(_task())
    updated = _task(question="Updated question.")
    store.save_task(updated)
    loaded = store.get_task("task_1")
    assert loaded.question == "Updated question."


def test_clear_removes_all_data(store):
    store.save_task(_task())
    store.save_document(_document())
    store.clear()
    assert store.get_task("task_1") is None
    assert store.get_document("doc_1") is None


def test_get_missing_returns_none(store):
    assert store.get_task("missing") is None
    assert store.get_document("missing") is None
    assert store.get_evidence("missing") is None
    assert store.get_claim("missing") is None
    assert store.get_verification_result("missing") is None
