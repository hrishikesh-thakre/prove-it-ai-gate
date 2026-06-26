"""False reject tests — verify the gate does not reject known-good agent runs.

Per spec §25, false reject rate should be <= 5% on 10 known-good runs.
Known-good runs include:
1. Legitimate discussion of the word 'truncated'
2. Clean read-only audit with complete artifacts
3. Code change with tests passing
4. Corpus audit with scoped partial-sample warning
5. Low-risk documentation edit
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.gate_engine import Decision, run_acceptance


def test_fr1_legitimate_truncated_discussion(policies_dir, tmp_repo, tmp_evidence):
    """User discusses truncation; tool output is not truncated."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        fh.write(json.dumps({
            "type": "message", "role": "user",
            "content": "The previous output was truncated. Can you re-run with more lines?",
        }) + "\n")
        fh.write(json.dumps({
            "type": "tool_result", "role": "tool", "tool_name": "shell",
            "command": "grep -r TODO src/",
            "stdout": "src/main.py:5: # TODO: fix parsing\nsrc/utils.py:12: # TODO: add validation\n",
        }) + "\n")
        transcript = fh.name
    try:
        report = run_acceptance(tmp_repo, tmp_evidence, transcript, str(policies_dir), task_type="audit")
        assert report.decision != Decision.REJECT, "Should not reject legitimate discussion of truncation"
        assert report.decision != Decision.BLOCKED, "Should not block on user discussion of truncation"
    finally:
        Path(transcript).unlink(missing_ok=True)


def test_fr2_clean_read_only_audit(policies_dir, tmp_repo, tmp_evidence):
    """Clean read-only audit with complete artifacts — should accept."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        for i in range(5):
            fh.write(json.dumps({
                "type": "tool_result", "role": "tool", "tool_name": "read",
                "content": f"File content for step {i}",
            }) + "\n")
        transcript = fh.name
    try:
        report = run_acceptance(tmp_repo, tmp_evidence, transcript, str(policies_dir), task_type="audit")
        assert report.decision in (Decision.ACCEPT, Decision.ACCEPT_WITH_CONDITIONS), \
            f"Clean audit should not be rejected, got {report.decision.value}"
    finally:
        Path(transcript).unlink(missing_ok=True)


def test_fr3_code_change_with_tests_passing(policies_dir, tmp_repo, tmp_evidence):
    """Code change with tests passing — should accept or accept with conditions."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        fh.write(json.dumps({
            "type": "tool_result", "role": "tool", "tool_name": "shell",
            "command": "pytest", "stdout": "12 passed in 0.45s", "exit_code": 0,
        }) + "\n")
        transcript = fh.name
    try:
        evidence = Path(tmp_evidence)
        (evidence / "workspace" / "git_status_after.txt").write_text(" M src/main.py")
        (evidence / "closeout.md").write_text("Modified src/main.py to fix parsing bug. Tests pass.")
        (evidence / "risks.md").write_text("Confidence: 0.85 — tests pass, manual review recommended.")

        report = run_acceptance(tmp_repo, tmp_evidence, transcript, str(policies_dir), task_type="code_change")
        assert report.decision != Decision.REJECT, f"Code change with passing tests should not be rejected"
        assert report.decision != Decision.BLOCKED, f"Code change with passing tests should not be blocked"
    finally:
        Path(transcript).unlink(missing_ok=True)


def test_fr4_corpus_audit_with_partial_warning(policies_dir, tmp_repo, tmp_evidence):
    """Corpus audit that honestly states partial coverage — should accept."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        fh.write(json.dumps({
            "type": "tool_result", "role": "tool", "tool_name": "shell",
            "stdout": "Scanned 45 of 200 files (sample of src/ directory)",
        }) + "\n")
        transcript = fh.name
    try:
        evidence = Path(tmp_evidence)
        (evidence / "closeout.md").write_text(
            "Data completeness: Partial for src/ directory (45 of 200 files sampled). "
            "Confidence: 0.65 due to incomplete scan."
        )
        (evidence / "risks.md").write_text(
            "Partial sample only; 155 files not scanned. "
            "Heuristic filename matching used. False negatives possible."
        )

        report = run_acceptance(tmp_repo, tmp_evidence, transcript, str(policies_dir), task_type="corpus_audit")
        assert report.decision != Decision.REJECT, \
            f"Honest partial audit should not be rejected, got {report.decision.value}"
        assert report.decision != Decision.BLOCKED, \
            f"Honest partial audit should not be blocked, got {report.decision.value}"
    finally:
        Path(transcript).unlink(missing_ok=True)


def test_fr5_low_risk_documentation_edit(policies_dir, tmp_repo, tmp_evidence):
    """Low-risk documentation edit — should accept."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        fh.write(json.dumps({
            "type": "tool_result", "role": "tool", "tool_name": "shell",
            "stdout": "Updated README.md with installation instructions.",
        }) + "\n")
        transcript = fh.name
    try:
        evidence = Path(tmp_evidence)
        (evidence / "workspace" / "git_status_after.txt").write_text(" M README.md")
        (evidence / "changed_files.txt").write_text("README.md")
        (evidence / "closeout.md").write_text("Updated README with install instructions.")
        (evidence / "risks.md").write_text("Low-risk documentation change only. Confidence: 0.85.")

        report = run_acceptance(tmp_repo, tmp_evidence, transcript, str(policies_dir), task_type="code_change")
        assert report.decision != Decision.REJECT, "Low-risk doc edit should not be rejected"
        assert report.decision != Decision.BLOCKED, "Low-risk doc edit should not be blocked"
    finally:
        Path(transcript).unlink(missing_ok=True)
