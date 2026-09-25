# ProofPilot Audit Report

An audit report is a structured, human-inspectable record of a ProofPilot
task run. It is generated deterministically from persisted data. It does
not rerun the pipeline and does not use an LLM.

## What an audit report contains

The report is produced by `AuditReportBuilder` (`app/audit/report.py`) and
contains:

- Task: task ID, question, status, creation and update timestamps, and
  the document IDs involved.
- Claims: every claim with its verification status, explanation, and the
  IDs of its supporting and conflicting evidence.
- Evidence: every evidence item with its document ID, chunk ID, source
  filename, page, excerpt, retrieval score, and verification status.
- Provenance: the full execution trail (step, actor, input and output
  references, detail, timestamp).
- Security events: events such as `contradiction_detected` and
  `insufficient_evidence` when they exist.
- Summary: deterministic counts of supported, contradicted, and
  insufficient claims.

## How claims connect to evidence

Each claim node exposes explicit relationship lists:

- `supporting_evidence_ids`: evidence that supports the claim.
- `conflicting_evidence_ids`: evidence that contradicts the claim.

The report also embeds the full evidence nodes under `supporting` and
`conflicting`, so a reader can see the excerpt, page, and source for each
relationship without joining tables manually.

## Evidence graph

The report renders a simple text graph showing the relationships:

```
Task
  +-- Claim (SUPPORTED)
  |     +-- SUPPORTS -> Evidence ev_1 (requirements.pdf)
  +-- Claim (CONTRADICTED)
        +-- CONTRADICTS -> Evidence ev_2 (policy.pdf)
```

This is a presentation of the same records already stored in SQLite; no
graph database is used.

## How contradictions are represented

When evidence directly contradicts a claim, the claim is marked
`CONTRADICTED` and the conflicting evidence IDs are recorded. The report
surfaces these explicitly in the Claims and Evidence Graph sections so the
conflict is inspectable rather than hidden.

## How provenance is represented

Every pipeline step is recorded as a `ProvenanceRecord` with meaningful
input and output references (task ID, document ID, chunk IDs, claim IDs,
evidence IDs). The report exposes these in a table with step, actor,
inputs, outputs, and timestamp.

## Why the report is deterministic

The report is built purely from persisted data using fixed aggregation
order. No randomness, no LLM, no network. Rendering the same report twice
produces identical output.

## Why the report does not imply that unsupported claims are true

A claim marked `SUPPORTED` only means the evidence text overlaps the claim
in a way the verifier considers supportive. A claim marked
`INSUFFICIENT_EVIDENCE` means the evidence does not establish the claim.
The report exposes both states explicitly and lists a Limitations section
stating that the deterministic verifier relies on lexical overlap and may
miss paraphrased contradictions.

## Example

```
# ProofPilot Audit Report

## Task

- Task ID: `task_123`
- Question: Does Company Alpha have ISO 9001 certification?
- Status: COMPLETED

## Verification Summary

- Supported: 1
- Contradicted: 0
- Insufficient evidence: 1
```

## Endpoints

- `GET /tasks/{task_id}/report`: structured JSON report.
- `GET /tasks/{task_id}/report.md`: the same report as Markdown.
