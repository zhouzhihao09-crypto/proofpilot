"""Task execution and retrieval routes."""

from fastapi import APIRouter, HTTPException, Request, Response

from app.api.schemas import (
    AuditReportResponse,
    DocumentResponse,
    TaskRequest,
    TaskResponse,
    TaskResultResponse,
)
from app.models import TaskStatus

router = APIRouter(tags=["tasks"])


@router.post("/tasks", response_model=TaskResponse)
def create_task(request: Request, body: TaskRequest) -> TaskResponse:
    """Submit a task for execution against already-ingested documents."""
    pipeline = request.app.state.pipeline
    store = request.app.state.store

    missing = [did for did in body.document_ids if store.get_document(did) is None]
    if body.document_ids and missing:
        raise HTTPException(
            status_code=404,
            detail=f"Documents not found: {', '.join(missing)}",
        )

    try:
        result = pipeline.run_with_documents(
            question=body.question,
            document_ids=body.document_ids,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Task execution failed: {exc}") from exc

    return TaskResponse(
        task_id=result.task_id,
        question=body.question,
        status=result.status,
        document_ids=result.provenance.get("document_ids", []),
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(request: Request, task_id: str) -> TaskResponse:
    """Return persisted task information."""
    store = request.app.state.store
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResponse(
        task_id=task.task_id,
        question=task.question,
        status=task.status,
        document_ids=task.document_ids,
    )


@router.get("/tasks/{task_id}/result", response_model=TaskResultResponse)
def get_task_result(request: Request, task_id: str) -> TaskResultResponse:
    """Return the full task result including claims, evidence, and provenance."""
    store = request.app.state.store
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    claims = store.get_claims_for_task(task_id)
    evidence = store.get_evidence_for_task(task_id)
    provenance_records = store.get_provenance_for_task(task_id)

    supported = sum(1 for c in claims if c.verification_status.value == "SUPPORTED")
    contradicted = sum(1 for c in claims if c.verification_status.value == "CONTRADICTED")
    insufficient = sum(1 for c in claims if c.verification_status.value == "INSUFFICIENT_EVIDENCE")
    summary = (
        f"Generated {len(claims)} claim(s): {supported} supported, "
        f"{contradicted} contradicted, {insufficient} insufficient evidence."
    )

    return TaskResultResponse(
        task_id=task.task_id,
        status=task.status,
        summary=summary,
        claims=claims,
        evidence=evidence,
        provenance={
            "task_id": task.task_id,
            "document_ids": task.document_ids,
            "steps": [r.step for r in provenance_records],
            "records": [r.model_dump() for r in provenance_records],
        },
    )


@router.get("/tasks/{task_id}/provenance")
def get_task_provenance(request: Request, task_id: str):
    """Return the auditable execution trail for a task."""
    store = request.app.state.store
    if store.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")
    records = store.get_provenance_for_task(task_id)
    return {
        "task_id": task_id,
        "records": [r.model_dump() for r in records],
    }


@router.get("/documents/{document_id}", response_model=DocumentResponse)
def get_document(request: Request, document_id: str) -> DocumentResponse:
    """Return persisted document metadata."""
    store = request.app.state.store
    document = store.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentResponse(
        document_id=document.document_id,
        filename=document.filename,
        mime_type=document.mime_type,
        page_count=document.page_count,
        status=document.status.value,
        metadata=document.metadata,
    )


@router.get("/tasks/{task_id}/report", response_model=AuditReportResponse)
def get_task_report(request: Request, task_id: str) -> AuditReportResponse:
    """Return the complete structured audit report for a task.

    This is a read/presentation layer over persisted data. It does not
    rerun the pipeline.
    """
    from app.audit.report import AuditReportBuilder

    store = request.app.state.store
    if store.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")

    builder = AuditReportBuilder(store)
    report = builder.build(task_id)
    return AuditReportResponse(**report.model_dump())


@router.get("/tasks/{task_id}/report.md")
def get_task_report_markdown(request: Request, task_id: str) -> Response:
    """Return the audit report rendered as deterministic Markdown."""
    from app.audit.markdown import render_markdown
    from app.audit.report import AuditReportBuilder

    store = request.app.state.store
    if store.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")

    builder = AuditReportBuilder(store)
    report = builder.build(task_id)
    return Response(
        content=render_markdown(report),
        media_type="text/markdown",
    )