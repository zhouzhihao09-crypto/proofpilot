"""Core data models for ProofPilot.

These Pydantic models define the internal evidence/claim data model.
They are intentionally explicit about provenance so that the execution
trail is auditable rather than buried inside generated text.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VerificationStatus(str, Enum):
    """Possible verification outcomes for a claim."""

    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class DocumentStatus(str, Enum):
    """Lifecycle state of an ingested document."""

    PENDING = "PENDING"
    EXTRACTING = "EXTRACTING"
    CHUNKED = "CHUNKED"
    INDEXED = "INDEXED"
    FAILED = "FAILED"


class TaskStatus(str, Enum):
    """Lifecycle state of a task run."""

    RECEIVED = "RECEIVED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip()).strip("_")
    return cleaned.lower() or "item"


class Document(BaseModel):
    """An ingested source document."""

    document_id: str
    filename: str
    mime_type: str = "application/pdf"
    page_count: int = 0
    status: DocumentStatus = DocumentStatus.PENDING
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, str] = Field(default_factory=dict)


class Chunk(BaseModel):
    """A contiguous slice of extracted document text."""

    chunk_id: str
    document_id: str
    page: Optional[int] = None
    text: str
    start_char: int = 0
    end_char: int = 0
    sequence: int = 0
    token_estimate: int = 0


class Evidence(BaseModel):
    """A single piece of evidence retrieved for a task.

    Evidence is kept separate from claims so that the relationship
    between what was said and what was claimed is always explicit.
    """

    evidence_id: str
    document_id: str
    chunk_id: Optional[str] = None
    claim_id: Optional[str] = None
    source: str = ""
    page: Optional[int] = None
    excerpt: str = ""
    retrieval_score: float = 0.0
    verification_status: Optional[VerificationStatus] = None
    created_at: datetime = Field(default_factory=_utcnow)

    @field_validator("excerpt")
    @classmethod
    def _nonempty_excerpt(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("evidence excerpt must not be empty")
        return value


class Claim(BaseModel):
    """A candidate claim generated from evidence.

    A claim is a statement that may be supported, contradicted, or
    found to have insufficient evidence.
    """

    claim_id: str
    task_id: str
    text: str
    verification_status: VerificationStatus = VerificationStatus.INSUFFICIENT_EVIDENCE
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)
    explanation: str = ""
    verification_method: Optional[VerificationMethod] = None
    deterministic_status: Optional[VerificationStatus] = None
    semantic_status: Optional[VerificationStatus] = None
    semantic_confidence: Optional[float] = None
    semantic_explanation: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @field_validator("text")
    @classmethod
    def _nonempty_text(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("claim text must not be empty")
        return value


class VerificationMethod(str, Enum):
    """How a claim was verified."""

    DETERMINISTIC = "DETERMINISTIC"
    SEMANTIC = "SEMANTIC"
    HYBRID = "HYBRID"


class VerificationResult(BaseModel):
    """Structured output of the verification stage.

    Optional metadata fields describe how the result was produced. They
    default to None so that existing deterministic results remain unchanged
    and serializable without semantic data.
    """

    claim_id: str
    status: VerificationStatus
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list)
    verification_method: Optional[VerificationMethod] = None
    deterministic_status: Optional[VerificationStatus] = None
    semantic_status: Optional[VerificationStatus] = None
    semantic_confidence: Optional[float] = None
    semantic_explanation: Optional[str] = None
    deterministic_confidence: Optional[float] = None


class Task(BaseModel):
    """A user task/question to be investigated."""

    task_id: str
    question: str
    document_ids: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.RECEIVED
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class TaskResult(BaseModel):
    """The final structured result for a task."""

    task_id: str
    status: TaskStatus
    summary: str = ""
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    provenance: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class ProvenanceRecord(BaseModel):
    """An auditable step in the execution trail."""

    record_id: str
    task_id: str
    step: str
    actor: str
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    detail: dict[str, object] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)