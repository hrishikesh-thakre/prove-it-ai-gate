from __future__ import annotations

import tempfile
from pathlib import Path

from prove_it_ai_gate.confidence_checker import check_confidence_claims


class TestConfidenceChecker:
    def test_high_confidence_without_evidence_blocks(self, tmp_evidence):
        evidence = Path(tmp_evidence)
        (evidence / "closeout.md").write_text("Confidence: 0.95 — complete audit.")
        (evidence / "artifacts" / "inventory.json").unlink()
        (evidence / "artifacts" / "extracted_findings.json").unlink()
        result = check_confidence_claims(str(evidence))
        assert result.status in ("blocked", "failed")
        assert result.details["max_confidence_found"] >= 0.90

    def test_100_percent_confidence_without_evidence_blocks(self, tmp_evidence):
        evidence = Path(tmp_evidence)
        (evidence / "closeout.md").write_text("I am 100% confident this is complete.")
        (evidence / "artifacts" / "inventory.json").unlink()
        (evidence / "artifacts" / "extracted_findings.json").unlink()
        result = check_confidence_claims(str(evidence))
        assert result.status in ("blocked", "failed")

    def test_moderate_confidence_passes(self, tmp_evidence):
        evidence = Path(tmp_evidence)
        (evidence / "closeout.md").write_text("Confidence: 0.75 — heuristic-based audit.")
        result = check_confidence_claims(str(evidence))
        assert result.status in ("passed", "warning")

    def test_heuristic_with_high_confidence_rejects(self, tmp_evidence):
        evidence = Path(tmp_evidence)
        (evidence / "closeout.md").write_text("Confidence: 0.92 — used grep to find all matches.")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
            fh.write('{"type":"tool_call","tool_name":"shell","command":"grep -r TODO src/"}\n')
            transcript = fh.name

        try:
            result = check_confidence_claims(str(evidence), transcript)
            assert result.details["max_confidence_found"] >= 0.90
            assert result.details["heuristic_methods_detected"] is True
        finally:
            Path(transcript).unlink(missing_ok=True)
