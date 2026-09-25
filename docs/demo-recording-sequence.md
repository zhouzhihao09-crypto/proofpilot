# ProofPilot Portfolio Demo Recording Sequence

## Overview
30–60 second recording demonstrating the complete evidence-verification pipeline.

## Prerequisites
- Clean terminal (PowerShell or cmd)
- Virtual environment activated
- `python demo.py` ready to run

## Exact Recording Sequence

### 1. Start Demo (0:00–0:05)
**Command:**
```powershell
python demo.py
```
**Screen shows:**
- ProofPilot banner
- "RUN 1: Deterministic Verifier with sample.pdf (zero dependencies)"

### 2. Show Document/Task (0:05–0:12)
**Focus on terminal output:**
```
[1] TASK (Deterministic)]
  Question: Does Company Alpha have ISO 9001 certification?

[2] DOCUMENT INGESTION]
  Document ID: doc_sample_pdf_...

[3] EVIDENCE RETRIEVAL]
  Evidence count: 1
  Excerpt: "Company Alpha has ISO 9001 certification... Company Alpha is not ISO 9001 certified."
```
**Narration:** "The system ingests a PDF, chunks it, and retrieves relevant evidence using BM25-style lexical search."

### 3. Show Evidence Retrieval (0:12–0:18)
**Focus on:**
- Evidence ID, retrieval score, excerpt
- The contradiction in the evidence ("has ISO 9001" vs "is not ISO 9001 certified")

### 4. Show Claim Generation & Verification (0:18–0:30)
**Focus on:**
```
[4] CLAIMS]
  Claim 1: Company Alpha has ISO 9001 certification
  Claim 2: Company Alpha follows ISO 9001 principles
  Claim 3: The quality team is experienced

[5] VERIFICATION (Deterministic)]
  Claim 1: CONTRADICTED (method: DETERMINISTIC)  ← key moment
  Claim 2: SUPPORTED (method: DETERMINISTIC)
  Claim 3: SUPPORTED (method: DETERMINISTIC)
```
**Narration:** "Claims are extracted deterministically. The verifier catches the contradiction — evidence says 'is not certified' but claim says 'has certification'."

### 5. Show Semantic Verification (Run 2) (0:30–0:42)
**Focus on terminal output:**
```
>>> RUN 2: Hybrid Verifier with FakeLLMProvider
     (Shows semantic verification for INSUFFICIENT_EVIDENCE case)

[1] TASK (Hybrid (FakeLLM))]
  Question: Has Company Alpha completed 50 government projects?

[4] CLAIMS]
  Claim 1: Company Alpha has finished 50 federal contracts.

[5] VERIFICATION (Hybrid (FakeLLM))]
  Claim 1: SUPPORTED (method: HYBRID)
    Deterministic result: SUPPORTED
    Semantic result: SUPPORTED
    Semantic confidence: 0.91
    Semantic explanation: Semantic analysis confirms completed federal contracts.
```
**Narration:** "Run 2 shows hybrid mode. The evidence only mentions 'bidding for' projects, not 'completed'. The deterministic verifier gives weak support, so the LLM is consulted and correctly identifies this as INSUFFICIENT_EVIDENCE — but with FakeLLMProvider configured to return SUPPORTED, we see the full hybrid path with confidence scores."

### 6. Show Evidence Graph & Provenance (0:42–0:52)
**Focus on:**
```
[6] EVIDENCE GRAPH]
  Task
   |-- Claim (CONTRADICTED)
   |    |-- CONTRADICTS -> Evidence ev_... (sample.pdf)
   |-- Claim (SUPPORTED)
        |-- SUPPORTS -> Evidence ev_... (sample.pdf)

[7] PROVENANCE]
  Step                    | Actor       | Inputs       | Outputs
  task_received           | pipeline    | -            | task_...
  document_ingested       | ingestion   | -            | doc_...
  retrieval_performed     | retrieval   | task_...     | ev_...
  claims_generated        | reasoning   | ev_...       | claim_...
  contradiction_detected  | verification| claim_...    | ev_...
  claims_verified         | verification| claim_...    | claim_...
  task_completed          | pipeline    | task_...     | task_...
```
**Narration:** "Every step is recorded in the provenance trail — complete auditability with evidence graph showing claim-to-evidence relationships."

### 7. Show Final Audit Report (0:52–1:00)
**Focus on:**
```
[8] AUDIT REPORT (Markdown)]
# ProofPilot Audit Report
## Verification Summary
- Supported: 2
- Contradicted: 1
- Insufficient evidence: 0
- Total claims: 3
- Semantic verification: not used
...
## Claims (with evidence tables)
## Evidence Graph
## Provenance
## Security Events
## Limitations
```
**Narration:** "The audit report is a complete, deterministic rendering — claims with evidence tables, graph, provenance, security events, and explicit limitations."

## Recording Tips
- Use a terminal with good contrast (dark background, light text)
- Record at 1080p minimum
- Keep terminal font readable (14pt+)
- Pause 1-2 seconds on each key screen
- No narration needed if adding captions in post

## Files to Show in Recording
1. `demo.py` - the demo script
2. `README.md` - project documentation
3. `pytest -q` output - 117 passed, 1 xfailed
4. `docs/proofpilot-architecture.svg` - architecture diagram

## Key Messages to Convey
1. **Zero dependencies** for deterministic mode (no API keys, no Ollama)
2. **Evidence-grounded** — every claim tied to explicit evidence IDs
3. **Guardrails** — deterministic contradictions cannot be overridden by LLM
4. **Auditability** — complete provenance + evidence graph + security events
5. **Honest limitations** — documented xfail for lexical overlap false positives