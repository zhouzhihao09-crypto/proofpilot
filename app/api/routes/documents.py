"""Document ingestion route."""

from fastapi import APIRouter, HTTPException, Request, UploadFile, File

from app.api.schemas import DocumentUploadResponse
from app.models import DocumentStatus

router = APIRouter(tags=["documents"])

ALLOWED_MIME_TYPES = {"application/pdf"}
ALLOWED_EXTENSIONS = {".pdf"}
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MiB


def _validate_upload(file: UploadFile, data: bytes) -> None:
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {MAX_FILE_SIZE} bytes",
        )
    filename = (file.filename or "").lower()
    mime = (file.content_type or "").lower()
    ext_ok = any(filename.endswith(ext) for ext in ALLOWED_EXTENSIONS)
    mime_ok = mime in ALLOWED_MIME_TYPES
    if not (ext_ok or mime_ok):
        raise HTTPException(
            status_code=400,
            detail="Only PDF documents are accepted",
        )


@router.post("/documents", response_model=DocumentUploadResponse)
def upload_document(request: Request, file: UploadFile = File(...)) -> DocumentUploadResponse:
    """Ingest an uploaded PDF document."""
    ingestion = request.app.state.ingestion
    store = request.app.state.store

    data = file.file.read()
    _validate_upload(file, data)

    try:
        document, chunks = ingestion.ingest(file.filename or "upload.pdf", data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to ingest document: {exc}") from exc

    document.status = DocumentStatus.INDEXED
    try:
        store.save_document(document)
        for chunk in chunks:
            store.save_chunk(chunk)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to persist document: {exc}") from exc

    return DocumentUploadResponse(
        document_id=document.document_id,
        filename=document.filename,
        mime_type=document.mime_type,
        page_count=document.page_count,
        chunk_count=len(chunks),
        status=document.status.value,
    )
