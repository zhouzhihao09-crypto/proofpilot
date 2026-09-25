"""HTTP-specific Pydantic schemas for the ProofPilot API.

These schemas keep HTTP concerns separate from the domain models.
Where possible they reuse the domain models rather than redefining them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models import Claim, Evidence, ProvenanceRecord, Task, TaskStatus, VerificationStatus


class AuditReportResponse(BaseModel):
    """Structured audit report response for GET /tasks/{task_id}/report."""

    task_id: str
    question: str
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    document_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    supported_count: int = 0
    contradicted_count: int = 0
    insufficient_count: int = 0
    claims: list[dict] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    provenance: list[dict] = Field(default_factory=list)
    security_events: list[dict] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"


class DocumentUploadResponse(BaseModel):
    document_id: str
    filename: str
    mime_type: str
    page_count: int
    chunk_count: int
    status: str


class TaskRequest(BaseModel):
    question: str
    document_ids: list[str] = Field(default_factory=list)
    top_k: int = 5


class TaskResponse(BaseModel):
    task_id: str
    question: str
    status: TaskStatus
    document_ids: list[str] = Field(default_factory=list)


class TaskResultResponse(BaseModel):
    task_id: str
    status: TaskStatus
    summary: str
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)


class ProvenanceListResponse(BaseModel):
    task_id: str
    records: list[ProvenanceRecord] = Field(default_factory=list)


class DocumentResponse(BaseModel):
    document_id: str
    filename: str
    mime_type: str
    page_count: int
    status: str
    metadata: dict = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    detail: str

