"""FastAPI application factory for ProofPilot.

The app is a thin HTTP layer over the existing ProofPilotPipeline.
It does not duplicate ingestion, retrieval, claim generation, or
verification logic.
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional

from fastapi import FastAPI

from app.core.pipeline import ProofPilotPipeline
from app.ingestion.service import IngestionService
from app.provenance.store import ProvenanceStore
from app.retrieval.retriever import KeywordRetriever
from app.verification.verifier import DeterministicVerifier
from app.reasoning.claim_generator import DeterministicClaimGenerator


def _build_verifier(verifier_mode: str, store: ProvenanceStore):
    """Build a verifier based on the configured mode.

    Args:
        verifier_mode: "deterministic" or "hybrid"
        store: ProvenanceStore instance (used for HybridVerifier if needed)

    Returns:
        A Verifier instance.

    Raises:
        ValueError: If verifier_mode is invalid or hybrid mode is selected
            without a valid LLM provider.
    """
    if verifier_mode == "deterministic":
        return DeterministicVerifier()
    if verifier_mode == "hybrid":
        # For hybrid mode, we require an explicit LLM provider to be set up
        # via dependency injection. The default app cannot load real LLM
        # providers without credentials. Fail clearly if no provider is
        # available in the environment.
        raise ValueError(
            "Hybrid verifier mode requires an explicit LLMProvider via "
            "dependency injection. Pass a custom pipeline with HybridVerifier "
            "to create_app()."
        )
    raise ValueError(f"Invalid VERIFIER_MODE: {verifier_mode}. Expected 'deterministic' or 'hybrid'.")


def create_app(
    db_path: Optional[str] = None,
    pipeline: Optional[ProofPilotPipeline] = None,
    verifier_mode: Optional[str] = None,
) -> FastAPI:
    """Create a FastAPI app with injectable dependencies.

    Parameters
    ----------
    db_path:
        Path to the SQLite database. Defaults to a temporary file so the
        app can run without configuration. Pass an explicit path for
        persistent storage.
    pipeline:
        An optional pre-built ProofPilotPipeline. Useful for tests that
        want to inject a temporary database and deterministic components.
    verifier_mode:
        Verifier mode: "deterministic" (default) or "hybrid". When
        "hybrid" is selected, an LLMProvider must be supplied via the
        pipeline parameter (explicit dependency injection takes precedence).
        If no pipeline is provided, hybrid mode will raise an error because
        the default app cannot load real LLM providers without credentials.

    The VERIFIER_MODE environment variable can also be used as a fallback
    when verifier_mode is not explicitly provided.
    """
    if db_path is None:
        db_path = os.path.join(tempfile.gettempdir(), "proofpilot.db")

    store = ProvenanceStore(db_path)

    # Determine verifier mode: explicit parameter > env var > default
    if verifier_mode is None:
        verifier_mode = os.environ.get("VERIFIER_MODE", "deterministic")

    if pipeline is None:
        verifier = _build_verifier(verifier_mode, store)
        pipeline = ProofPilotPipeline(
            ingestion=IngestionService(),
            retriever=KeywordRetriever(),
            claim_generator=DeterministicClaimGenerator(),
            verifier=verifier,
            store=store,
        )
    else:
        # Use the pipeline's store for consistency
        store = pipeline.store

    app = FastAPI(
        title="ProofPilot",
        description=(
            "Evidence-grounded AI task execution and verification. "
            "Ingest documents, submit tasks, and inspect an auditable "
            "evidence trail."
        ),
        version="0.1.0",
    )

    # Attach dependencies to the app state so routes can access them.
    app.state.store = store
    app.state.pipeline = pipeline
    app.state.ingestion = pipeline.ingestion

    from app.api.routes import health, documents, tasks

    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(tasks.router)

    return app


# A module-level app instance for `uvicorn app.api.app:app`.
app = create_app()
