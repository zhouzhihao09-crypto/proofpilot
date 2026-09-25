"""Audit report models and builder for ProofPilot.

The audit report turns persisted evidence, verification, and provenance
data into a structured, human-inspectable record. It is a read/presentation
layer over the existing ProvenanceStore; it does not rerun the pipeline.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from pydantic import BaseModel, Field

from app.models import (
    Claim,
    Document,
    Evidence,
    ProvenanceRecord,
    Task,
    VerificationMethod,
    VerificationStatus,
)


class EvidenceNode(BaseModel):
    """A node in the evidence graph representing one evidence item."""

    evidence_id: str
    document_id: str
    chunk_id: Optional[str] = None
    filename: str = ""
    page: Optional[int] = None
    excerpt: str = ""
    retrieval_score: float = 0.0
    verification_status: Optional[VerificationStatus] = None


class ClaimNode(BaseModel):
    """A node in the evidence graph representing one claim."""

    claim_id: str
    text: str
    verification_status: VerificationStatus
    explanation: str = ""
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)
    supporting: list[EvidenceNode] = Field(default_factory=list)
    conflicting: list[EvidenceNode] = Field(default_factory=list)
    verification_method: Optional[VerificationMethod] = None
    deterministic_status: Optional[VerificationStatus] = None
    semantic_status: Optional[VerificationStatus] = None
    semantic_confidence: Optional[float] = None
    semantic_explanation: Optional[str] = None


class ProvenanceNode(BaseModel):
    """A provenance record exposed in the audit report."""

    record_id: str
    step: str
    actor: str
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    detail: dict = Field(default_factory=dict)
    timestamp: Optional[datetime] = None


class SecurityEvent(BaseModel):
    """A security/reliability event recorded during the pipeline."""

    record_id: str
    step: str
    claim_id: Optional[str] = None
    detail: dict = Field(default_factory=dict)


class AuditReport(BaseModel):
    """The complete structured audit report for a task."""

    task_id: str
    question: str
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    document_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    claims: list[ClaimNode] = Field(default_factory=list)
    evidence: list[EvidenceNode] = Field(default_factory=list)
    provenance: list[ProvenanceNode] = Field(default_factory=list)
    security_events: list[SecurityEvent] = Field(default_factory=list)
    supported_count: int = 0
    contradicted_count: int = 0
    insufficient_count: int = 0
    used_semantic_verification: bool = False


class AuditReportBuilder:
    """Builds a deterministic AuditReport from the ProvenanceStore."""

    SECURITY_EVENT_STEPS = ("contradiction_detected", "insufficient_evidence")

    def __init__(self, store) -> None:
        self.store = store

    def build(self, task_id: str) -> AuditReport:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")

        claims = self.store.get_claims_for_task(task_id)
        evidence = self.store.get_evidence_for_task(task_id)
        provenance = self.store.get_provenance_for_task(task_id)

        evidence_by_id = {e.evidence_id: e for e in evidence}
        documents_by_id = {
            d.document_id: d
            for d in (self.store.get_document(did) for did in task.document_ids)
            if d
        }

        evidence_nodes = [self._evidence_node(e, documents_by_id) for e in evidence]
        evidence_nodes_by_id = {n.evidence_id: n for n in evidence_nodes}

        claim_nodes: list[ClaimNode] = []
        used_semantic = False
        for claim in claims:
            supporting = [
                evidence_nodes_by_id[eid]
                for eid in claim.supporting_evidence_ids
                if eid in evidence_nodes_by_id
            ]
            conflicting = [
                evidence_nodes_by_id[eid]
                for eid in claim.conflicting_evidence_ids
                if eid in evidence_nodes_by_id
            ]
            # Fetch persisted verification metadata for this claim.
            vr = self.store.get_verification_result(claim.claim_id)
            verification_method = None
            deterministic_status = None
            semantic_status = None
            semantic_confidence = None
            semantic_explanation = None
            if vr is not None:
                verification_method = vr.verification_method
                deterministic_status = vr.deterministic_status
                semantic_status = vr.semantic_status
                semantic_confidence = vr.semantic_confidence
                semantic_explanation = vr.semantic_explanation
                if verification_method == VerificationMethod.HYBRID:
                    used_semantic = True
            claim_nodes.append(
                ClaimNode(
                    claim_id=claim.claim_id,
                    text=claim.text,
                    verification_status=claim.verification_status,
                    explanation=claim.explanation,
                    supporting_evidence_ids=claim.supporting_evidence_ids,
                    conflicting_evidence_ids=claim.conflicting_evidence_ids,
                    supporting=supporting,
                    conflicting=conflicting,
                    verification_method=verification_method,
                    deterministic_status=deterministic_status,
                    semantic_status=semantic_status,
                    semantic_confidence=semantic_confidence,
                    semantic_explanation=semantic_explanation,
                )
            )

        provenance_nodes = [self._provenance_node(r) for r in provenance]
        security_events = [
            SecurityEvent(
                record_id=r.record_id,
                step=r.step,
                claim_id=r.detail.get("claim_id") if isinstance(r.detail, dict) else None,
                detail=r.detail if isinstance(r.detail, dict) else {},
            )
            for r in provenance
            if r.step in self.SECURITY_EVENT_STEPS
        ]

        supported = sum(1 for c in claim_nodes if c.verification_status == VerificationStatus.SUPPORTED)
        contradicted = sum(1 for c in claim_nodes if c.verification_status == VerificationStatus.CONTRADICTED)
        insufficient = sum(1 for c in claim_nodes if c.verification_status == VerificationStatus.INSUFFICIENT_EVIDENCE)
        summary = (
            f"Generated {len(claim_nodes)} claim(s): {supported} supported, "
            f"{contradicted} contradicted, {insufficient} insufficient evidence."
        )

        return AuditReport(
            task_id=task.task_id,
            question=task.question,
            status=task.status.value,
            created_at=task.created_at,
            updated_at=task.updated_at,
            document_ids=list(task.document_ids),
            summary=summary,
            claims=claim_nodes,
            evidence=evidence_nodes,
            provenance=provenance_nodes,
            security_events=security_events,
            supported_count=supported,
            contradicted_count=contradicted,
            insufficient_count=insufficient,
            used_semantic_verification=used_semantic,
        )

    def _evidence_node(self, evidence: Evidence, documents_by_id: dict[str, Document]) -> EvidenceNode:
        document = documents_by_id.get(evidence.document_id)
        return EvidenceNode(
            evidence_id=evidence.evidence_id,
            document_id=evidence.document_id,
            chunk_id=evidence.chunk_id,
            filename=document.filename if document else evidence.document_id,
            page=evidence.page,
            excerpt=evidence.excerpt,
            retrieval_score=evidence.retrieval_score,
            verification_status=evidence.verification_status,
        )

    @staticmethod
    def _provenance_node(record: ProvenanceRecord) -> ProvenanceNode:
        return ProvenanceNode(
            record_id=record.record_id,
            step=record.step,
            actor=record.actor,
            input_refs=list(record.input_refs),
            output_refs=list(record.output_refs),
            detail=dict(record.detail) if isinstance(record.detail, dict) else {},
            timestamp=record.timestamp,
        )
