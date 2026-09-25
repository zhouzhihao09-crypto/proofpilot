"""SQLite-backed persistence for ProofPilot.

The ProvenanceStore persists the domain objects defined in
``app.models`` using a normalized SQLite schema. It is intentionally
focused on persistence only; it contains no business logic, retrieval,
or verification logic.

Structured list/dict fields (e.g. ``document_ids``, ``metadata``,
``supporting_evidence_ids``) are serialized as JSON. Everything else is
stored in dedicated columns so relationships remain queryable.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Sequence

from app.models import (
    Claim,
    Document,
    DocumentStatus,
    Evidence,
    ProvenanceRecord,
    Task,
    TaskStatus,
    VerificationMethod,
    VerificationResult,
    VerificationStatus,
)


def _serialize_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _json_dumps(value) -> str:
    return json.dumps(value, default=str)


def _json_loads(value: Optional[str]):
    if value is None:
        return []
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []


class ProvenanceStore:
    """Persists ProofPilot domain objects to a SQLite database."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------
    def _ensure_schema(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        existing = self._conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?", ("version",)
        ).fetchone()
        version = 0
        if existing:
            try:
                version = int(existing[0])
            except (TypeError, ValueError):
                version = 0
        if version < 1:
            self._create_schema_v1()
            self._conn.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
                ("version", "1"),
            )
            self._conn.commit()
            version = 1
        if version < 2:
            self._migrate_to_v2()
            self._conn.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
                ("version", "2"),
            )
            self._conn.commit()

    def _migrate_to_v2(self) -> None:
        """Add verification metadata columns to verification_results.

        Uses ALTER TABLE ADD COLUMN with defaults so existing databases
        can be upgraded without data loss. The new columns are nullable.
        """
        new_columns = {
            "verification_method": "TEXT",
            "deterministic_status": "TEXT",
            "semantic_status": "TEXT",
            "semantic_confidence": "REAL",
            "semantic_explanation": "TEXT",
            "deterministic_confidence": "REAL",
        }
        for column, col_type in new_columns.items():
            try:
                self._conn.execute(
                    f"ALTER TABLE verification_results ADD COLUMN {column} {col_type}"
                )
            except sqlite3.OperationalError:
                # Column already exists; safe to ignore.
                pass
        
        # Add verification metadata columns to claims table
        claim_columns = {
            "verification_method": "TEXT",
            "deterministic_status": "TEXT",
            "semantic_status": "TEXT",
            "semantic_confidence": "REAL",
            "semantic_explanation": "TEXT",
        }
        for column, col_type in claim_columns.items():
            try:
                self._conn.execute(
                    f"ALTER TABLE claims ADD COLUMN {column} {col_type}"
                )
            except sqlite3.OperationalError:
                # Column already exists; safe to ignore.
                pass
        
        self._conn.commit()

    def _create_schema_v1(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                document_id   TEXT PRIMARY KEY,
                filename      TEXT NOT NULL,
                mime_type     TEXT NOT NULL,
                page_count    INTEGER NOT NULL DEFAULT 0,
                status        TEXT NOT NULL,
                metadata      TEXT NOT NULL DEFAULT '{}',
                created_at    TEXT,
                updated_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS tasks (
                task_id       TEXT PRIMARY KEY,
                question      TEXT NOT NULL,
                document_ids  TEXT NOT NULL DEFAULT '[]',
                status        TEXT NOT NULL,
                created_at    TEXT,
                updated_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id      TEXT PRIMARY KEY,
                document_id   TEXT NOT NULL,
                page          INTEGER,
                text          TEXT NOT NULL,
                start_char    INTEGER NOT NULL DEFAULT 0,
                end_char      INTEGER NOT NULL DEFAULT 0,
                sequence      INTEGER NOT NULL DEFAULT 0,
                token_estimate INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (document_id) REFERENCES documents(document_id)
                    ON DELETE CASCADE ON UPDATE CASCADE
            );

            CREATE TABLE IF NOT EXISTS evidence (
                evidence_id       TEXT PRIMARY KEY,
                document_id       TEXT NOT NULL,
                chunk_id          TEXT,
                claim_id          TEXT,
                source            TEXT NOT NULL DEFAULT '',
                page              INTEGER,
                excerpt           TEXT NOT NULL,
                retrieval_score   REAL NOT NULL DEFAULT 0.0,
                verification_status TEXT,
                created_at        TEXT,
                FOREIGN KEY (document_id) REFERENCES documents(document_id)
                    ON DELETE CASCADE ON UPDATE CASCADE,
                FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id)
                    ON DELETE SET NULL ON UPDATE CASCADE,
                FOREIGN KEY (claim_id) REFERENCES claims(claim_id)
                    ON DELETE SET NULL ON UPDATE CASCADE
            );

            CREATE TABLE IF NOT EXISTS claims (
                claim_id                TEXT PRIMARY KEY,
                task_id                 TEXT NOT NULL,
                text                    TEXT NOT NULL,
                verification_status      TEXT NOT NULL,
                supporting_evidence_ids TEXT NOT NULL DEFAULT '[]',
                conflicting_evidence_ids TEXT NOT NULL DEFAULT '[]',
                explanation              TEXT NOT NULL DEFAULT '',
                verification_method      TEXT,
                deterministic_status     TEXT,
                semantic_status          TEXT,
                semantic_confidence      REAL,
                semantic_explanation     TEXT,
                created_at              TEXT,
                updated_at              TEXT,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id)
                    ON DELETE CASCADE ON UPDATE CASCADE
            );

            CREATE TABLE IF NOT EXISTS verification_results (
                claim_id              TEXT PRIMARY KEY,
                status                TEXT NOT NULL,
                explanation           TEXT NOT NULL,
                evidence_ids          TEXT NOT NULL DEFAULT '[]',
                verification_method   TEXT,
                deterministic_status  TEXT,
                semantic_status       TEXT,
                semantic_confidence   REAL,
                semantic_explanation  TEXT,
                deterministic_confidence REAL
            );

            CREATE TABLE IF NOT EXISTS provenance (
                record_id    TEXT PRIMARY KEY,
                task_id      TEXT NOT NULL,
                step         TEXT NOT NULL,
                actor        TEXT NOT NULL,
                input_refs   TEXT NOT NULL DEFAULT '[]',
                output_refs  TEXT NOT NULL DEFAULT '[]',
                detail       TEXT NOT NULL DEFAULT '{}',
                timestamp     TEXT,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id)
                    ON DELETE CASCADE ON UPDATE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_evidence_document ON evidence(document_id);
            CREATE INDEX IF NOT EXISTS idx_evidence_claim ON evidence(claim_id);
            CREATE INDEX IF NOT EXISTS idx_claims_task ON claims(task_id);
            CREATE INDEX IF NOT EXISTS idx_provenance_task ON provenance(task_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
            """
        )

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def commit(self) -> None:
        self._conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------
    def save_document(self, document: Document) -> Document:
        self._conn.execute(
            """
            INSERT INTO documents
                (document_id, filename, mime_type, page_count, status, metadata,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                filename=excluded.filename,
                mime_type=excluded.mime_type,
                page_count=excluded.page_count,
                status=excluded.status,
                metadata=excluded.metadata,
                updated_at=excluded.updated_at
            """,
            (
                document.document_id,
                document.filename,
                document.mime_type,
                document.page_count,
                document.status.value,
                _json_dumps(document.metadata),
                _serialize_datetime(document.created_at),
                _serialize_datetime(document.updated_at),
            ),
        )
        self._conn.commit()
        return document

    def get_document(self, document_id: str) -> Optional[Document]:
        row = self._conn.execute(
            "SELECT document_id, filename, mime_type, page_count, status, metadata, "
            "created_at, updated_at FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        return self._row_to_document(row) if row else None

    def get_documents(self) -> list[Document]:
        rows = self._conn.execute(
            "SELECT document_id, filename, mime_type, page_count, status, metadata, "
            "created_at, updated_at FROM documents ORDER BY created_at"
        ).fetchall()
        return [self._row_to_document(r) for r in rows]

    @staticmethod
    def _row_to_document(row) -> Document:
        return Document(
            document_id=row[0],
            filename=row[1],
            mime_type=row[2],
            page_count=row[3],
            status=DocumentStatus(row[4]),
            metadata=_json_loads(row[5]) or {},
            created_at=_parse_datetime(row[6]) or datetime.now(timezone.utc),
            updated_at=_parse_datetime(row[7]) or datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------
    def save_task(self, task: Task) -> Task:
        self._conn.execute(
            """
            INSERT INTO tasks
                (task_id, question, document_ids, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                question=excluded.question,
                document_ids=excluded.document_ids,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (
                task.task_id,
                task.question,
                _json_dumps(task.document_ids),
                task.status.value,
                _serialize_datetime(task.created_at),
                _serialize_datetime(task.updated_at),
            ),
        )
        self._conn.commit()
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        row = self._conn.execute(
            "SELECT task_id, question, document_ids, status, created_at, updated_at "
            "FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return self._row_to_task(row) if row else None

    def get_tasks(self) -> list[Task]:
        rows = self._conn.execute(
            "SELECT task_id, question, document_ids, status, created_at, updated_at "
            "FROM tasks ORDER BY created_at"
        ).fetchall()
        return [self._row_to_task(r) for r in rows]

    @staticmethod
    def _row_to_task(row) -> Task:
        return Task(
            task_id=row[0],
            question=row[1],
            document_ids=_json_loads(row[2]) or [],
            status=TaskStatus(row[3]),
            created_at=_parse_datetime(row[4]) or datetime.now(timezone.utc),
            updated_at=_parse_datetime(row[5]) or datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Chunks
    # ------------------------------------------------------------------
    def save_chunk(self, chunk) -> None:
        from app.models import Chunk

        self._conn.execute(
            """
            INSERT INTO chunks
                (chunk_id, document_id, page, text, start_char, end_char,
                 sequence, token_estimate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                document_id=excluded.document_id,
                page=excluded.page,
                text=excluded.text,
                start_char=excluded.start_char,
                end_char=excluded.end_char,
                sequence=excluded.sequence,
                token_estimate=excluded.token_estimate
            """,
            (
                chunk.chunk_id,
                chunk.document_id,
                chunk.page,
                chunk.text,
                chunk.start_char,
                chunk.end_char,
                chunk.sequence,
                chunk.token_estimate,
            ),
        )
        self._conn.commit()

    def get_chunks_for_document(self, document_id: str) -> list:
        """Return all chunks for a document, in sequence order."""
        from app.models import Chunk

        rows = self._conn.execute(
            "SELECT chunk_id, document_id, page, text, start_char, end_char, "
            "sequence, token_estimate FROM chunks WHERE document_id = ? "
            "ORDER BY sequence",
            (document_id,),
        ).fetchall()
        chunks = []
        for row in rows:
            chunks.append(
                Chunk(
                    chunk_id=row[0],
                    document_id=row[1],
                    page=row[2],
                    text=row[3],
                    start_char=row[4],
                    end_char=row[5],
                    sequence=row[6],
                    token_estimate=row[7],
                )
            )
        return chunks

    def get_chunk(self, chunk_id: str):
        from app.models import Chunk

        row = self._conn.execute(
            "SELECT chunk_id, document_id, page, text, start_char, end_char, "
            "sequence, token_estimate FROM chunks WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        if not row:
            return None
        return Chunk(
            chunk_id=row[0],
            document_id=row[1],
            page=row[2],
            text=row[3],
            start_char=row[4],
            end_char=row[5],
            sequence=row[6],
            token_estimate=row[7],
        )

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------
    def save_evidence(self, evidence: Evidence) -> Evidence:
        self._conn.execute(
            """
            INSERT INTO evidence
                (evidence_id, document_id, chunk_id, claim_id, source, page,
                 excerpt, retrieval_score, verification_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(evidence_id) DO UPDATE SET
                document_id=excluded.document_id,
                chunk_id=excluded.chunk_id,
                claim_id=excluded.claim_id,
                source=excluded.source,
                page=excluded.page,
                excerpt=excluded.excerpt,
                retrieval_score=excluded.retrieval_score,
                verification_status=excluded.verification_status,
                created_at=excluded.created_at
            """,
            (
                evidence.evidence_id,
                evidence.document_id,
                evidence.chunk_id,
                evidence.claim_id,
                evidence.source,
                evidence.page,
                evidence.excerpt,
                evidence.retrieval_score,
                evidence.verification_status.value if evidence.verification_status else None,
                _serialize_datetime(evidence.created_at),
            ),
        )
        self._conn.commit()
        return evidence

    def get_evidence(self, evidence_id: str) -> Optional[Evidence]:
        row = self._conn.execute(
            "SELECT evidence_id, document_id, chunk_id, claim_id, source, page, "
            "excerpt, retrieval_score, verification_status, created_at "
            "FROM evidence WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        return self._row_to_evidence(row) if row else None

    def get_evidence_for_task(self, task_id: str) -> list[Evidence]:
        """Return evidence linked to a task.

        Evidence is linked to a task in two ways:
        1. through a claim that belongs to the task (``claim_id``), or
        2. through a document that belongs to the task (``document_id``).
        """
        rows = self._conn.execute(
            """
            SELECT DISTINCT e.evidence_id, e.document_id, e.chunk_id,
                   e.claim_id, e.source, e.page, e.excerpt,
                   e.retrieval_score, e.verification_status, e.created_at
            FROM evidence e
            LEFT JOIN claims c ON e.claim_id = c.claim_id
            WHERE c.task_id = ?
               OR e.document_id IN (
                   SELECT value FROM tasks, json_each(tasks.document_ids)
                   WHERE tasks.task_id = ?
               )
            ORDER BY e.created_at
            """,
            (task_id, task_id),
        ).fetchall()
        return [self._row_to_evidence(r) for r in rows]

    @staticmethod
    def _row_to_evidence(row) -> Evidence:
        status = row[8]
        return Evidence(
            evidence_id=row[0],
            document_id=row[1],
            chunk_id=row[2],
            claim_id=row[3],
            source=row[4] or "",
            page=row[5],
            excerpt=row[6],
            retrieval_score=row[7] or 0.0,
            verification_status=VerificationStatus(status) if status else None,
            created_at=_parse_datetime(row[9]) or datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Claims
    # ------------------------------------------------------------------
    def save_claim(self, claim: Claim) -> Claim:
        self._conn.execute(
            """
            INSERT INTO claims
                (claim_id, task_id, text, verification_status,
                 supporting_evidence_ids, conflicting_evidence_ids,
                 explanation, verification_method, deterministic_status,
                 semantic_status, semantic_confidence, semantic_explanation,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(claim_id) DO UPDATE SET
                task_id=excluded.task_id,
                text=excluded.text,
                verification_status=excluded.verification_status,
                supporting_evidence_ids=excluded.supporting_evidence_ids,
                conflicting_evidence_ids=excluded.conflicting_evidence_ids,
                explanation=excluded.explanation,
                verification_method=excluded.verification_method,
                deterministic_status=excluded.deterministic_status,
                semantic_status=excluded.semantic_status,
                semantic_confidence=excluded.semantic_confidence,
                semantic_explanation=excluded.semantic_explanation,
                updated_at=excluded.updated_at
            """,
            (
                claim.claim_id,
                claim.task_id,
                claim.text,
                claim.verification_status.value,
                _json_dumps(claim.supporting_evidence_ids),
                _json_dumps(claim.conflicting_evidence_ids),
                claim.explanation,
                claim.verification_method.value if claim.verification_method else None,
                claim.deterministic_status.value if claim.deterministic_status else None,
                claim.semantic_status.value if claim.semantic_status else None,
                claim.semantic_confidence,
                claim.semantic_explanation,
                _serialize_datetime(claim.created_at),
                _serialize_datetime(claim.updated_at),
            ),
        )
        self._conn.commit()
        return claim

    def get_claim(self, claim_id: str) -> Optional[Claim]:
        row = self._conn.execute(
            "SELECT claim_id, task_id, text, verification_status, "
            "supporting_evidence_ids, conflicting_evidence_ids, explanation, "
            "verification_method, deterministic_status, semantic_status, "
            "semantic_confidence, semantic_explanation, "
            "created_at, updated_at FROM claims WHERE claim_id = ?",
            (claim_id,),
        ).fetchone()
        return self._row_to_claim(row) if row else None

    def get_claims_for_task(self, task_id: str) -> list[Claim]:
        rows = self._conn.execute(
            "SELECT claim_id, task_id, text, verification_status, "
            "supporting_evidence_ids, conflicting_evidence_ids, explanation, "
            "verification_method, deterministic_status, semantic_status, "
            "semantic_confidence, semantic_explanation, "
            "created_at, updated_at FROM claims WHERE task_id = ? "
            "ORDER BY created_at",
            (task_id,),
        ).fetchall()
        return [self._row_to_claim(r) for r in rows]

    @staticmethod
    def _row_to_claim(row) -> Claim:
        return Claim(
            claim_id=row[0],
            task_id=row[1],
            text=row[2],
            verification_status=VerificationStatus(row[3]),
            supporting_evidence_ids=_json_loads(row[4]) or [],
            conflicting_evidence_ids=_json_loads(row[5]) or [],
            explanation=row[6] or "",
            verification_method=VerificationMethod(row[7]) if row[7] else None,
            deterministic_status=VerificationStatus(row[8]) if row[8] else None,
            semantic_status=VerificationStatus(row[9]) if row[9] else None,
            semantic_confidence=row[10],
            semantic_explanation=row[11] or None,
            created_at=_parse_datetime(row[12]) or datetime.now(timezone.utc),
            updated_at=_parse_datetime(row[13]) or datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Verification results
    # ------------------------------------------------------------------
    def save_verification_result(self, result: VerificationResult) -> VerificationResult:
        self._conn.execute(
            """
            INSERT INTO verification_results
                (claim_id, status, explanation, evidence_ids,
                 verification_method, deterministic_status, semantic_status,
                 semantic_confidence, semantic_explanation, deterministic_confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(claim_id) DO UPDATE SET
                status=excluded.status,
                explanation=excluded.explanation,
                evidence_ids=excluded.evidence_ids,
                verification_method=excluded.verification_method,
                deterministic_status=excluded.deterministic_status,
                semantic_status=excluded.semantic_status,
                semantic_confidence=excluded.semantic_confidence,
                semantic_explanation=excluded.semantic_explanation,
                deterministic_confidence=excluded.deterministic_confidence
            """,
            (
                result.claim_id,
                result.status.value,
                result.explanation,
                _json_dumps(result.evidence_ids),
                result.verification_method.value if result.verification_method else None,
                result.deterministic_status.value if result.deterministic_status else None,
                result.semantic_status.value if result.semantic_status else None,
                result.semantic_confidence,
                result.semantic_explanation,
                result.deterministic_confidence,
            ),
        )
        self._conn.commit()
        return result

    def get_verification_result(self, claim_id: str) -> Optional[VerificationResult]:
        row = self._conn.execute(
            "SELECT claim_id, status, explanation, evidence_ids, "
            "verification_method, deterministic_status, semantic_status, "
            "semantic_confidence, semantic_explanation, deterministic_confidence "
            "FROM verification_results WHERE claim_id = ?",
            (claim_id,),
        ).fetchone()
        if not row:
            return None
        return VerificationResult(
            claim_id=row[0],
            status=VerificationStatus(row[1]),
            explanation=row[2] or "",
            evidence_ids=_json_loads(row[3]) or [],
            verification_method=VerificationMethod(row[4]) if row[4] else None,
            deterministic_status=VerificationStatus(row[5]) if row[5] else None,
            semantic_status=VerificationStatus(row[6]) if row[6] else None,
            semantic_confidence=row[7],
            semantic_explanation=row[8] or None,
            deterministic_confidence=row[9],
        )

    # ------------------------------------------------------------------
    # Provenance records
    # ------------------------------------------------------------------
    def save_provenance(self, record: ProvenanceRecord) -> ProvenanceRecord:
        self._conn.execute(
            """
            INSERT INTO provenance
                (record_id, task_id, step, actor, input_refs, output_refs,
                 detail, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
                task_id=excluded.task_id,
                step=excluded.step,
                actor=excluded.actor,
                input_refs=excluded.input_refs,
                output_refs=excluded.output_refs,
                detail=excluded.detail,
                timestamp=excluded.timestamp
            """,
            (
                record.record_id,
                record.task_id,
                record.step,
                record.actor,
                _json_dumps(record.input_refs),
                _json_dumps(record.output_refs),
                _json_dumps(record.detail),
                _serialize_datetime(record.timestamp),
            ),
        )
        self._conn.commit()
        return record

    def get_provenance_for_task(self, task_id: str) -> list[ProvenanceRecord]:
        rows = self._conn.execute(
            "SELECT record_id, task_id, step, actor, input_refs, output_refs, "
            "detail, timestamp FROM provenance WHERE task_id = ? "
            "ORDER BY timestamp",
            (task_id,),
        ).fetchall()
        return [self._row_to_provenance(r) for r in rows]

    @staticmethod
    def _row_to_provenance(row) -> ProvenanceRecord:
        return ProvenanceRecord(
            record_id=row[0],
            task_id=row[1],
            step=row[2],
            actor=row[3],
            input_refs=_json_loads(row[4]) or [],
            output_refs=_json_loads(row[5]) or [],
            detail=_json_loads(row[6]) or {},
            timestamp=_parse_datetime(row[7]) or datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Bulk / convenience helpers
    # ------------------------------------------------------------------
    def save_task_full(
        self,
        task: Task,
        documents: Sequence[Document] = (),
        evidence: Sequence[Evidence] = (),
        claims: Sequence[Claim] = (),
        verification_results: Sequence[VerificationResult] = (),
        provenance: Sequence[ProvenanceRecord] = (),
    ) -> Task:
        """Persist a task and all of its related objects in one call."""
        self.save_task(task)
        for document in documents:
            self.save_document(document)
        for claim in claims:
            self.save_claim(claim)
        for item in evidence:
            self.save_evidence(item)
        for result in verification_results:
            self.save_verification_result(result)
        for record in provenance:
            self.save_provenance(record)
        return task

    def get_task_full(self, task_id: str) -> Optional[dict]:
        """Load a task with all related objects as a dictionary."""
        task = self.get_task(task_id)
        if task is None:
            return None
        return {
            "task": task,
            "documents": [self.get_document(did) for did in task.document_ids],
            "claims": self.get_claims_for_task(task_id),
            "evidence": self.get_evidence_for_task(task_id),
            "provenance": self.get_provenance_for_task(task_id),
        }

    def clear(self) -> None:
        """Delete all data from every table. Useful for tests."""
        for table in (
            "provenance",
            "verification_results",
            "evidence",
            "claims",
            "chunks",
            "tasks",
            "documents",
        ):
            self._conn.execute(f"DELETE FROM {table}")
        self._conn.commit()
