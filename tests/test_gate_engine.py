from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from prove_it_ai_gate.gate_engine import (
    Decision,
    run_acceptance,
    resolve_policy,
    compute_decision,
    CheckResult,
    Issue,
    Severity,
)


class TestPolicyResolution:
    def test_resolve_by_task_type(self, policies_dir):
        policy, name = resolve_policy("audit", str(policies_dir))
        assert policy["name"] == "read_only_audit"

    def test_resolve_by_explicit_name(self, policies_dir):
        policy, name = resolve_policy("unknown_type", str(policies_dir), "code_change")
        assert policy["name"] == "code_change"

    def test_resolve_unknown_fails(self, policies_dir):
        with pytest.raises(FileNotFoundError):
            resolve_policy("nonexistent_type", str(policies_dir))


class TestDecisionModel:
    def test_all_passed_is_accept(self):
        results = [
            CheckResult(check_name="test", status="passed"),
        ]
        policy = {"reject_if": [], "block_if": []}
        assert compute_decision(results, policy) == Decision.ACCEPT

    def test_blocker_issue_returns_blocked(self):
        results = [
            CheckResult(
                check_name="test",
                status="blocked",
                issues=[Issue("test", Severity.BLOCKER, "missing evidence")],
            ),
        ]
        policy = {"reject_if": [], "block_if": []}
        assert compute_decision(results, policy) == Decision.BLOCKED

    def test_reject_issue_returns_reject(self):
        results = [
            CheckResult(
                check_name="test",
                status="failed",
                issues=[Issue("test", Severity.REJECT, "repo modified")],
            ),
        ]
        policy = {"reject_if": [], "block_if": []}
        assert compute_decision(results, policy) == Decision.REJECT

    def test_warning_returns_accept_with_conditions(self):
        results = [
            CheckResult(
                check_name="test",
                status="warning",
                issues=[Issue("test", Severity.WARNING, "scratch file detected")],
            ),
        ]
        policy = {"reject_if": [], "block_if": []}
        assert compute_decision(results, policy) == Decision.ACCEPT_WITH_CONDITIONS


class TestEndToEnd:
    def test_run_acceptance_on_valid_evidence(self, tmp_repo, tmp_evidence, tmp_transcript, policies_dir):
        report = run_acceptance(
            repo_path=tmp_repo,
            evidence_path=tmp_evidence,
            transcript_path=tmp_transcript,
            policy_dir=str(policies_dir),
            task_type="audit",
        )
        assert report.decision in (Decision.ACCEPT, Decision.ACCEPT_WITH_CONDITIONS)

    def test_run_acceptance_with_truncated_transcript(self, tmp_repo, tmp_evidence, truncated_transcript, policies_dir):
        report = run_acceptance(
            repo_path=tmp_repo,
            evidence_path=tmp_evidence,
            transcript_path=truncated_transcript,
            policy_dir=str(policies_dir),
            task_type="audit",
        )
        assert report.decision in (Decision.BLOCKED, Decision.REJECT)

    def test_run_acceptance_on_read_only_modification(self, tmp_repo, tmp_evidence, tmp_transcript, policies_dir):
        evidence = Path(tmp_evidence)
        ws_dir = evidence / "workspace"
        (ws_dir / "git_status_after.txt").write_text(" M src/main.py")
        (evidence / "closeout.md").write_text("No files changed. Audit complete.")

        report = run_acceptance(
            repo_path=tmp_repo,
            evidence_path=tmp_evidence,
            transcript_path=tmp_transcript,
            policy_dir=str(policies_dir),
            task_type="audit",
        )
        assert report.decision in (Decision.REJECT, Decision.BLOCKED)
