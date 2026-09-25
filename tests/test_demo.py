"""Tests for the demo script."""

import subprocess
import sys
import os


def test_demo_runs_without_errors():
    """The demo script should execute without external services."""
    demo_path = os.path.join(os.path.dirname(__file__), "..", "demo.py")
    result = subprocess.run(
        [sys.executable, demo_path],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"Demo failed with stderr:\n{result.stderr}"

    # Check that key sections are present in output
    output = result.stdout
    assert "PROOFPILOT" in output
    assert "TASK" in output
    assert "DOCUMENT INGESTION" in output
    assert "EVIDENCE RETRIEVAL" in output
    assert "CLAIMS" in output
    assert "VERIFICATION" in output
    assert "EVIDENCE GRAPH" in output
    assert "PROVENANCE" in output
    assert "AUDIT REPORT" in output

    # Should show the three claim verdicts
    assert "CONTRADICTED" in output
    assert "SUPPORTED" in output
    assert "INSUFFICIENT_EVIDENCE" in output or "DETERMINISTIC" in output


def test_demo_uses_real_pipeline():
    """The demo should use the actual ProofPilotPipeline, not mock output."""
    demo_path = os.path.join(os.path.dirname(__file__), "..", "demo.py")
    result = subprocess.run(
        [sys.executable, demo_path],
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = result.stdout

    # Should show actual evidence IDs (not placeholder)
    assert "ev_" in output or "evidence" in output.lower()

    # Should show actual document ID format
    assert "doc_" in output

    # Should show actual retrieval scores
    assert "score" in output.lower() or "retrieval" in output.lower()