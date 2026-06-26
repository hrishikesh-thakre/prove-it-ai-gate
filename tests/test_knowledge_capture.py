from __future__ import annotations

import tempfile
from pathlib import Path

from src.gate_engine import Decision
from src.types import CheckResult, Issue, Severity
from src.knowledge_capture import generate_capture, write_capture, capture_from_json_report


class TestKnowledgeCapture:
    def test_generate_capture_from_accepted_report(self, tmp_evidence):
        from src.gate_engine import AcceptanceReport

        report = AcceptanceReport(
            decision=Decision.ACCEPT,
            task_type="audit",
            policy_name="read_only_audit",
            repo_path="./fake",
            evidence_path=tmp_evidence,
            transcript_path="./transcript.jsonl",
            generated_at="2026-06-26T20:00:00",
            checks=[
                CheckResult(check_name="transcript_truncation_check", status="passed"),
                CheckResult(check_name="workspace_hygiene_check", status="passed"),
            ],
            failed_checks=[],
            blocked_checks=[],
            warning_checks=[],
            required_next_action="No action required.",
        )

        content = generate_capture(report, tmp_evidence)
        assert "type: project_learning" in content
        assert "Decision: `ACCEPT`" in content
        assert "What Worked" in content
        assert "What Did Not Work" in content
        assert "Reusable Insight" in content

    def test_generate_capture_from_rejected_report(self, tmp_evidence):
        from src.gate_engine import AcceptanceReport

        report = AcceptanceReport(
            decision=Decision.REJECT,
            task_type="audit",
            policy_name="read_only_audit",
            repo_path="./fake",
            evidence_path=tmp_evidence,
            transcript_path="./transcript.jsonl",
            generated_at="2026-06-26T20:00:00",
            checks=[
                CheckResult(check_name="workspace_hygiene_check", status="failed",
                            issues=[Issue("workspace_hygiene_check", Severity.REJECT,
                                          "Target repo was modified during read-only task")]),
            ],
            failed_checks=["workspace_hygiene_check"],
            blocked_checks=[],
            warning_checks=[],
            required_next_action="Restore target repo and rerun.",
        )

        content = generate_capture(report, tmp_evidence)
        assert "Decision: `REJECT`" in content
        assert "Target repo was modified" in content
        assert "failure" in content

    def test_write_capture_to_file(self, tmp_evidence):
        from src.gate_engine import AcceptanceReport

        report = AcceptanceReport(
            decision=Decision.ACCEPT_WITH_CONDITIONS,
            task_type="code_change",
            policy_name="code_change",
            repo_path="./fake",
            evidence_path=tmp_evidence,
            transcript_path="./transcript.jsonl",
            generated_at="2026-06-26T20:00:00",
            checks=[
                CheckResult(check_name="workspace_hygiene_check", status="warning",
                            issues=[Issue("workspace_hygiene_check", Severity.WARNING,
                                          "Scratch file detected")]),
            ],
            failed_checks=[],
            blocked_checks=[],
            warning_checks=["workspace_hygiene_check"],
            required_next_action="Address warnings before next stage.",
        )

        with tempfile.TemporaryDirectory() as out_dir:
            path = write_capture(report, tmp_evidence, out_dir)
            assert path.is_file()
            content = path.read_text()
            assert "ACCEPT_WITH_CONDITIONS" in content
            assert "conditional" in content

    def test_capture_from_json_report(self, tmp_evidence):
        import json

        report_json = {
            "decision": "REJECT",
            "task_type": "audit",
            "policy_name": "read_only_audit",
            "repo_path": "./fake",
            "evidence_path": tmp_evidence,
            "transcript_path": "./t.jsonl",
            "generated_at": "2026-06-26T20:00:00",
            "checks": [
                {"check_name": "test_check", "status": "failed",
                 "issues": [{"severity": "reject", "message": "Test failure"}],
                 "details": {}}
            ],
            "summary": {
                "failed_checks": ["test_check"],
                "blocked_checks": [],
                "warning_checks": [],
            },
            "required_next_action": "Fix and resubmit.",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "report.json"
            json_path.write_text(json.dumps(report_json))

            capture_path = capture_from_json_report(str(json_path), tmp_evidence, tmpdir)
            assert capture_path.is_file()
            content = capture_path.read_text()
            assert "Decision: `REJECT`" in content
