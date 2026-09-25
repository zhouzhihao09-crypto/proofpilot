"""Deterministic Markdown renderer for ProofPilot audit reports.

The renderer is a pure presentation function: it takes an AuditReport
and produces deterministic Markdown text. It does not call the LLM, the
pipeline, or any external service.
"""

from __future__ import annotations

from app.audit.report import AuditReport


def _escape_table_cell(value: object) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|")
    return text


def render_markdown(report: AuditReport) -> str:
    """Render an AuditReport as deterministic Markdown text."""
    lines: list[str] = []

    lines.append("# ProofPilot Audit Report")
    lines.append("")
    lines.append("## Task")
    lines.append("")
    lines.append(f"- Task ID: `{report.task_id}`")
    lines.append(f"- Question: {report.question}")
    lines.append(f"- Status: {report.status}")
    if report.created_at is not None:
        lines.append(f"- Created: {report.created_at.isoformat()}")
    if report.updated_at is not None:
        lines.append(f"- Updated: {report.updated_at.isoformat()}")
    if report.document_ids:
        lines.append("- Documents:")
        for document_id in report.document_ids:
            lines.append(f"  - `{document_id}`")
    lines.append("")

    lines.append("## Verification Summary")
    lines.append("")
    lines.append(f"- Supported: {report.supported_count}")
    lines.append(f"- Contradicted: {report.contradicted_count}")
    lines.append(f"- Insufficient evidence: {report.insufficient_count}")
    lines.append(f"- Total claims: {len(report.claims)}")
    if report.used_semantic_verification:
        lines.append("- Semantic verification: used")
    else:
        lines.append("- Semantic verification: not used")
    lines.append("")
    lines.append(f"> {report.summary}")
    lines.append("")

    lines.append("## Claims")
    lines.append("")
    if not report.claims:
        lines.append("_No claims were generated._")
    else:
        for index, claim in enumerate(report.claims, start=1):
            lines.append(f"### Claim {index}")
            lines.append("")
            lines.append(f"- Claim ID: `{claim.claim_id}`")
            lines.append(f"- Status: {claim.verification_status.value}")
            if claim.verification_method is not None:
                lines.append(f"- Verification method: {claim.verification_method.value}")
            if claim.deterministic_status is not None:
                lines.append(f"- Deterministic result: {claim.deterministic_status.value}")
            if claim.semantic_status is not None:
                lines.append(f"- Semantic result: {claim.semantic_status.value}")
            if claim.semantic_confidence is not None:
                lines.append(f"- Semantic confidence: {claim.semantic_confidence}")
            if claim.semantic_explanation:
                lines.append(f"- Semantic explanation: {claim.semantic_explanation}")
            if claim.explanation:
                lines.append(f"- Explanation: {claim.explanation}")
            lines.append("")
            lines.append("> " + claim.text.replace("\n", " "))
            lines.append("")

            if claim.supporting:
                lines.append("#### Supporting Evidence")
                lines.append("")
                lines.append("| Evidence ID | Document | Page | Excerpt |")
                lines.append("|-------------|----------|------|---------|")
                for evidence in claim.supporting:
                    lines.append(
                        "| `{}` | {} | {} | {} |".format(
                            _escape_table_cell(evidence.evidence_id),
                            _escape_table_cell(evidence.filename),
                            _escape_table_cell(evidence.page if evidence.page is not None else "-"),
                            _escape_table_cell(evidence.excerpt),
                        )
                    )
                lines.append("")

            if claim.conflicting:
                lines.append("#### Conflicting Evidence")
                lines.append("")
                lines.append("| Evidence ID | Document | Page | Excerpt |")
                lines.append("|-------------|----------|------|---------|")
                for evidence in claim.conflicting:
                    lines.append(
                        "| `{}` | {} | {} | {} |".format(
                            _escape_table_cell(evidence.evidence_id),
                            _escape_table_cell(evidence.filename),
                            _escape_table_cell(evidence.page if evidence.page is not None else "-"),
                            _escape_table_cell(evidence.excerpt),
                        )
                    )
                lines.append("")

            if not claim.supporting and not claim.conflicting:
                lines.append("_No evidence was attached to this claim._")
                lines.append("")

    lines.append("## Evidence")
    lines.append("")
    if not report.evidence:
        lines.append("_No evidence was retrieved._")
    else:
        lines.append("| Evidence ID | Document | Page | Score | Excerpt |")
        lines.append("|-------------|----------|------|-------|---------|")
        for evidence in report.evidence:
            lines.append(
                "| `{}` | {} | {} | {} | {} |".format(
                    _escape_table_cell(evidence.evidence_id),
                    _escape_table_cell(evidence.filename),
                    _escape_table_cell(evidence.page if evidence.page is not None else "-"),
                    _escape_table_cell(round(evidence.retrieval_score, 4)),
                    _escape_table_cell(evidence.excerpt),
                )
            )
    lines.append("")

    lines.append("## Evidence Graph")
    lines.append("")
    lines.append("```text")
    lines.append("Task")
    for claim in report.claims:
        branch = "SUPPORTS" if claim.supporting else ("CONTRADICTS" if claim.conflicting else "NO EVIDENCE")
        lines.append(f" └── Claim ({claim.verification_status.value})")
        if claim.supporting:
            lines.append(f"      ├── SUPPORTS -> Evidence")
            for evidence in claim.supporting:
                lines.append(f"      │    └── {evidence.evidence_id} ({evidence.filename})")
        if claim.conflicting:
            lines.append(f"      └── CONTRADICTS -> Evidence")
            for evidence in claim.conflicting:
                lines.append(f"           └── {evidence.evidence_id} ({evidence.filename})")
    lines.append("```")
    lines.append("")

    lines.append("## Provenance")
    lines.append("")
    if not report.provenance:
        lines.append("_No provenance records were captured._")
    else:
        lines.append("| Step | Actor | Inputs | Outputs | Timestamp |")
        lines.append("|------|-------|--------|---------|-----------|")
        for record in report.provenance:
            timestamp = record.timestamp.isoformat() if record.timestamp else "-"
            lines.append(
                "| {} | {} | {} | {} | {} |".format(
                    _escape_table_cell(record.step),
                    _escape_table_cell(record.actor),
                    _escape_table_cell(", ".join(record.input_refs) if record.input_refs else "-"),
                    _escape_table_cell(", ".join(record.output_refs) if record.output_refs else "-"),
                    _escape_table_cell(timestamp),
                )
            )
    lines.append("")

    lines.append("## Security Events")
    lines.append("")
    if not report.security_events:
        lines.append("_No security events were recorded._")
    else:
        for event in report.security_events:
            lines.append(f"- `{event.step}` (record `{event.record_id}`)")
    lines.append("")

    lines.append("## Limitations")
    lines.append("")
    lines.append("- This report is generated deterministically from persisted data.")
    lines.append("- It does not imply that unsupported or insufficient claims are true.")
    lines.append("- The deterministic verifier relies on lexical overlap and may miss paraphrased contradictions.")
    lines.append("- A claim marked SUPPORTED only means the evidence text overlaps the claim; it is not independent verification.")
    lines.append("")

    return "\n".join(lines)
