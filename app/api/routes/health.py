"""Health check route."""

from fastapi import APIRouter

from app.api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Return a simple status indicating the service is running."""
    return HealthResponse(status="ok")
