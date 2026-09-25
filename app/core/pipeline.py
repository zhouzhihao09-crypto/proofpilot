"""End-to-end pipeline orchestrator for ProofPilot.

The ProofPilotPipeline coordinates the existing deterministic components
into one complete local workflow:

    Task -> ingest -> index -> retrieve -> evidence -> claims ->
    verify -> persist -> TaskResult

It does not implement BM25, PDF extraction, or verification logic; it
only orchestrates the existing components.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from app.ingestion.service import IngestionService
from app.models import (
    Chunk,
    Claim,
    Document,
    DocumentStatus,
    Evidence,
    ProvenanceRecord,
    Task,
    TaskResult,
    TaskStatus,
    VerificationMethod,
    VerificationResult,
    VerificationStatus,
)
from app.provenance.store import ProvenanceStore
from app.reasoning.claim_generator import ClaimGenerator, DeterministicClaimGenerator
from app.retrieval.retriever import KeywordRetriever, Retriever
from app.verification.verifier import DeterministicVerifier, Verifier


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProofPilotPipeline:
    """Coordinates ingestion, retrieval, claims, verification, and persistence."""

    def __init__(
        self,
        ingestion: Optional[IngestionService] = None,
        retriever: Optional[Retriever] = None,
        claim_generator: Optional[ClaimGenerator] = None,
        verifier: Optional[Verifier] = None,
        store: Optional[ProvenanceStore] = None,
        top_k: int = 5,
    ) -> None:
        self.ingestion = ingestion or IngestionService()
        self.retriever = retriever or KeywordRetriever()
        self.claim_generator = claim_generator or DeterministicClaimGenerator()
        self.verifier = verifier or DeterministicVerifier()
        self.store = store
        self.top_k = top_k

    def run(self, question: str, documents: Sequence[tuple[str, bytes]], task_id: Optional[str] = None) -> TaskResult:
        """Run the full pipeline for a task and its documents.

        If any major step fails, the task is persisted as FAILED and a
        failed TaskResult is returned rather than an unhandled exception.
        """
        store = self.store or ProvenanceStore(":memory:")
        owns_store = self.store is None
        task = Task(
            task_id=task_id or _new_id("task"),
            question=question,
            document_ids=[],
            status=TaskStatus.RECEIVED,
        )
        try:
            store.save_task(task)
            self._record(
                store, task.task_id, "task_received", "pipeline",
                input_refs=[],
                output_refs=[task.task_id],
                detail={"question": question, "document_count": len(documents)},
            )
            result = self._run(question, documents, task_id, task, store)
            return result
        except Exception as exc:
            return self._fail(store, task, f"pipeline failed: {exc}")
        finally:
            if owns_store:
                store.close()

    def run_with_documents(
        self,
        question: str,
        document_ids: Sequence[str],
        task_id: Optional[str] = None,
    ) -> TaskResult:
        """Run the pipeline against already-ingested documents.

        Loads chunks for each document from the store, then runs the
        retrieval -> evidence -> claims -> verification flow. This is the
        entry point used by the API when documents have already been
        uploaded.
        """
        store = self.store or ProvenanceStore(":memory:")
        owns_store = self.store is None
        task = Task(
            task_id=task_id or _new_id("task"),
            question=question,
            document_ids=list(document_ids),
            status=TaskStatus.RECEIVED,
        )
        try:
            store.save_task(task)
            self._record(
                store, task.task_id, "task_received", "pipeline",
                input_refs=[],
                output_refs=[task.task_id],
                detail={"question": question, "document_count": len(document_ids)},
            )
            result = self._run_with_documents(question, document_ids, task_id, task, store)
            return result
        except Exception as exc:
            return self._fail(store, task, f"pipeline failed: {exc}")
        finally:
            if owns_store:
                store.close()

    def _run_with_documents(self, question, document_ids, task_id, task, store) -> TaskResult:
        try:
            task.status = TaskStatus.RUNNING
            store.save_task(task)
        except Exception as exc:
            return self._fail(store, task, f"failed to mark task running: {exc}")

        all_chunks: list[Chunk] = []
        try:
            for document_id in document_ids:
                document = store.get_document(document_id)
                if document is None:
                    return self._fail(store, task, f"document not found: {document_id}")
                chunks = store.get_chunks_for_document(document_id)
                all_chunks.extend(chunks)
                self._record(
                    store, task.task_id, "document_ingested", "ingestion",
                    input_refs=[],
                    output_refs=[document.document_id],
                    detail={
                        "filename": document.filename,
                        "page_count": document.page_count,
                        "chunk_count": len(chunks),
                    },
                )
            store.save_task(task)
        except Exception as exc:
            return self._fail(store, task, f"document loading failed: {exc}")

        return self._run_retrieve_and_verify(question, task, store, all_chunks, list(document_ids))

    def _run_retrieve_and_verify(self, question, task, store, all_chunks, document_ids) -> TaskResult:
        """Shared retrieval -> evidence -> claims -> verification flow.

        Used by both ``run`` (fresh ingestion) and ``run_with_documents``
        (already-ingested documents) after chunks have been loaded.
        """
        if not all_chunks:
            result = TaskResult(
                task_id=task.task_id,
                status=TaskStatus.COMPLETED,
                summary="No documents were provided; no claims could be generated.",
                claims=[],
                evidence=[],
                provenance={"steps": ["task_received", "document_ingested"]},
            )
            self._record(
                store, task.task_id, "task_completed", "pipeline",
                input_refs=[task.task_id],
                output_refs=[task.task_id],
                detail={"claim_count": 0, "status": "COMPLETED"},
            )
            task.status = TaskStatus.COMPLETED
            store.save_task(task)
            return result

        # --- Retrieval -------------------------------------------------
        self.retriever.index(all_chunks)
        try:
            retrieved = self.retriever.retrieve(question, top_k=self.top_k)
        except Exception as exc:
            return self._fail(store, task, f"retrieval failed: {exc}")

        evidence_items: list[Evidence] = []
        for chunk, score in retrieved:
            evidence = Evidence(
                evidence_id=_new_id("ev"),
                document_id=chunk.document_id,
                chunk_id=chunk.chunk_id,
                claim_id=None,
                source=chunk.text,
                page=chunk.page,
                excerpt=chunk.text,
                retrieval_score=score,
            )
            evidence_items.append(evidence)
            store.save_evidence(evidence)
        self._record(
            store, task.task_id, "retrieval_performed", "retrieval",
            input_refs=[task.task_id],
            output_refs=[e.evidence_id for e in evidence_items],
            detail={"evidence_count": len(evidence_items), "top_k": self.top_k},
        )

        # --- Claim generation ------------------------------------------
        # The claim generator consumes Chunk objects (it reads .text),
        # so we pass the retrieved chunks rather than Evidence objects.
        try:
            claims = self.claim_generator.generate(task, [chunk for chunk, _ in retrieved])
        except Exception as exc:
            return self._fail(store, task, f"claim generation failed: {exc}")

        for claim in claims:
            store.save_claim(claim)
        self._record(
            store, task.task_id, "claims_generated", "reasoning",
            input_refs=[e.evidence_id for e in evidence_items],
            output_refs=[c.claim_id for c in claims],
            detail={"claim_count": len(claims)},
        )

        # --- Verification ----------------------------------------------
        verified: list[Claim] = []
        results: list[VerificationResult] = []
        for claim in claims:
            relevant = [e for e in evidence_items if self._evidence_relevant(claim, e, all_chunks)]
            if not relevant:
                relevant = list(evidence_items)
            try:
                result = self.verifier.verify(claim, relevant)
            except Exception as exc:
                return self._fail(store, task, f"verification failed for {claim.claim_id}: {exc}")
            results.append(result)
            store.save_verification_result(result)
            claim.verification_status = result.status
            claim.verification_method = result.verification_method
            claim.deterministic_status = result.deterministic_status
            claim.semantic_status = result.semantic_status
            claim.semantic_confidence = result.semantic_confidence
            claim.semantic_explanation = result.semantic_explanation
            claim.supporting_evidence_ids = result.evidence_ids if result.status == VerificationStatus.SUPPORTED else []
            claim.conflicting_evidence_ids = result.evidence_ids if result.status == VerificationStatus.CONTRADICTED else []
            claim.explanation = result.explanation
            verified.append(claim)
            store.save_claim(claim)
            # Record a security/reliability event for each verification outcome.
            if result.status == VerificationStatus.CONTRADICTED:
                self._record(
                    store, task.task_id, "contradiction_detected", "verification",
                    input_refs=[claim.claim_id],
                    output_refs=result.evidence_ids,
                    detail={
                        "claim_text": claim.text,
                        "explanation": result.explanation,
                        "verification_method": result.verification_method.value if result.verification_method else None,
                        "deterministic_status": result.deterministic_status.value if result.deterministic_status else None,
                        "semantic_status": result.semantic_status.value if result.semantic_status else None,
                    },
                )
            elif result.status == VerificationStatus.INSUFFICIENT_EVIDENCE:
                self._record(
                    store, task.task_id, "insufficient_evidence", "verification",
                    input_refs=[claim.claim_id],
                    output_refs=[],
                    detail={
                        "claim_text": claim.text,
                        "explanation": result.explanation,
                        "verification_method": result.verification_method.value if result.verification_method else None,
                        "deterministic_status": result.deterministic_status.value if result.deterministic_status else None,
                        "semantic_status": result.semantic_status.value if result.semantic_status else None,
                    },
                )
            # Record semantic verification activity when the LLM was consulted.
            if result.verification_method == VerificationMethod.HYBRID:
                self._record(
                    store, task.task_id, "semantic_verification_used", "hybrid_verifier",
                    input_refs=[claim.claim_id],
                    output_refs=result.evidence_ids,
                    detail={
                        "claim_text": claim.text,
                        "deterministic_status": result.deterministic_status.value if result.deterministic_status else None,
                        "semantic_status": result.semantic_status.value if result.semantic_status else None,
                        "final_status": result.status.value,
                        "semantic_confidence": result.semantic_confidence,
                        "semantic_explanation": result.semantic_explanation,
                    },
                )
            # Link evidence to the claim where applicable.
            for evidence_id in result.evidence_ids:
                ev = next((e for e in evidence_items if e.evidence_id == evidence_id), None)
                if ev is not None and ev.claim_id is None:
                    ev.claim_id = claim.claim_id
                    store.save_evidence(ev)
        self._record(
            store, task.task_id, "claims_verified", "verification",
            input_refs=[c.claim_id for c in claims],
            output_refs=[r.claim_id for r in results],
            detail={"verification_result_count": len(results)},
        )

        # --- Complete --------------------------------------------------
        summary = self._summarize(verified)
        task.status = TaskStatus.COMPLETED
        store.save_task(task)
        self._record(
            store, task.task_id, "task_completed", "pipeline",
            input_refs=[task.task_id],
            output_refs=[task.task_id],
            detail={
                "claim_count": len(verified),
                "status": "COMPLETED",
                "summary": summary,
            },
        )

        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            summary=summary,
            claims=verified,
            evidence=evidence_items,
            provenance={
                "task_id": task.task_id,
                "document_ids": document_ids,
                "steps": [
                    "task_received",
                    "document_ingested",
                    "retrieval_performed",
                    "claims_generated",
                    "claims_verified",
                    "task_completed",
                ],
            },
        )

    # ------------------------------------------------------------------
    # Internal pipeline execution (fresh document bytes)
    # ------------------------------------------------------------------
    def _run(self, question, documents, task_id, task, store) -> TaskResult:
        try:
            task.status = TaskStatus.RUNNING
            store.save_task(task)
        except Exception as exc:
            return self._fail(store, task, f"failed to mark task running: {exc}")

        # --- Ingestion -------------------------------------------------
        all_chunks: list[Chunk] = []
        document_ids: list[str] = []
        try:
            for filename, data in documents:
                document, chunks = self.ingestion.ingest(filename, data)
                document.status = DocumentStatus.INDEXED
                store.save_document(document)
                for chunk in chunks:
                    store.save_chunk(chunk)
                document_ids.append(document.document_id)
                all_chunks.extend(chunks)
                self._record(
                    store, task.task_id, "document_ingested", "ingestion",
                    input_refs=[],
                    output_refs=[document.document_id],
                    detail={
                        "filename": document.filename,
                        "page_count": document.page_count,
                        "chunk_count": len(chunks),
                    },
                )
            task.document_ids = document_ids
            store.save_task(task)
        except Exception as exc:
            return self._fail(store, task, f"ingestion failed: {exc}")

        return self._run_retrieve_and_verify(question, task, store, all_chunks, document_ids)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _record(self, store, task_id, step, actor, input_refs, output_refs, detail) -> None:
        record = ProvenanceRecord(
            record_id=_new_id("rec"),
            task_id=task_id,
            step=step,
            actor=actor,
            input_refs=list(input_refs),
            output_refs=list(output_refs),
            detail=dict(detail),
        )
        store.save_provenance(record)

    def _fail(self, store, task: Task, message: str) -> TaskResult:
        task.status = TaskStatus.FAILED
        task.updated_at = _utcnow()
        try:
            store.save_task(task)
        except Exception:
            pass
        self._record(
            store, task.task_id, "task_failed", "pipeline",
            input_refs=[task.task_id],
            output_refs=[],
            detail={"error": message},
        )
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.FAILED,
            summary=f"Pipeline failed: {message}",
            claims=[],
            evidence=[],
            provenance={"task_id": task.task_id, "steps": ["task_received", "task_failed"]},
        )

    @staticmethod
    def _evidence_relevant(claim: Claim, evidence: Evidence, chunks: Sequence[Chunk]) -> bool:
        """Decide whether a piece of evidence is relevant to a claim.

        Evidence is relevant when it shares tokens with the claim. This
        keeps the verifier focused on related evidence rather than the
        entire corpus.
        """
        claim_tokens = {t for t in re.findall(r"[a-zA-Z0-9]+", claim.text.lower()) if t}
        if not claim_tokens:
            return True
        excerpt_tokens = {t for t in re.findall(r"[a-zA-Z0-9]+", evidence.excerpt.lower()) if t}
        return len(claim_tokens & excerpt_tokens) > 0

    @staticmethod
    def _summarize(claims: Sequence[Claim]) -> str:
        supported = sum(1 for c in claims if c.verification_status == VerificationStatus.SUPPORTED)
        contradicted = sum(1 for c in claims if c.verification_status == VerificationStatus.CONTRADICTED)
        insufficient = sum(1 for c in claims if c.verification_status == VerificationStatus.INSUFFICIENT_EVIDENCE)
        total = len(claims)
        return (
            f"Generated {total} claim(s): {supported} supported, "
            f"{contradicted} contradicted, {insufficient} insufficient evidence."
        )
