from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .types import Decision, Severity, Issue, CheckResult


@dataclass
class AcceptanceReport:
    decision: Decision
    task_type: str
    policy_name: str
    repo_path: str
    evidence_path: str
    transcript_path: str
    generated_at: str
    checks: list[CheckResult] = field(default_factory=list)
    failed_checks: list[str] = field(default_factory=list)
    blocked_checks: list[str] = field(default_factory=list)
    warning_checks: list[str] = field(default_factory=list)
    required_next_action: str = ""


def load_policy(policy_path: str) -> dict:
    with open(policy_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def resolve_policy(task_type: str, policy_dir: str, policy_name: str | None = None) -> tuple[dict, str]:
    if policy_name:
        path = os.path.join(policy_dir, policy_name)
        if not os.path.isfile(path):
            path = os.path.join(policy_dir, f"{policy_name}.yml")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Policy not found: {policy_name} in {policy_dir}")
        return load_policy(path), os.path.basename(path)

    for entry in sorted(Path(policy_dir).glob("*.yml")):
        policy = load_policy(str(entry))
        applies = policy.get("applies_to", [])
        if task_type in applies:
            return policy, entry.name
    raise FileNotFoundError(f"No policy found for task_type={task_type} in {policy_dir}")


def classify_risk(task_type: str, policy: dict) -> str:
    return policy.get("risk_tier", "medium")


def evaluate_checks(
    repo_path: str,
    evidence_path: str,
    transcript_path: str,
    policy: dict,
    task_type: str,
    scratch_dir: str = "",
) -> list[CheckResult]:
    from .transcript_checker import check_transcript_truncation
    from .workspace_guard import check_workspace_hygiene
    from .evidence_checker import check_evidence_folder
    from .confidence_checker import check_confidence_claims
    from .scope_completeness_checker import check_scope_completeness
    from .heuristic_checker import check_heuristic_extraction
    from .transcript_crosscheck import check_closeout_vs_transcript
    from .evidence_integrity import (
        check_inventory_vs_transcript,
        check_closeout_vs_validation,
        check_evidence_depth,
        check_git_status_authenticity,
    )

    thresholds = policy.get("confidence_thresholds", {})
    high_threshold = float(thresholds.get("high", 0.90))
    heuristic_cap = float(thresholds.get("heuristic_cap", 0.75))

    required_checks = policy.get("required_checks", [])
    results: list[CheckResult] = []

    check_registry = {
        "transcript_truncation_check": lambda: check_transcript_truncation(transcript_path),
        "workspace_hygiene_check": lambda: check_workspace_hygiene(repo_path, task_type, evidence_path, scratch_dir),
        "evidence_folder_schema_check": lambda: check_evidence_folder(evidence_path, task_type),
        "deterministic_evidence_artifact": lambda: _check_deterministic_artifact(evidence_path),
        "no_repo_modification_unless_requested": lambda: _check_no_repo_modification(repo_path, task_type, evidence_path),
        "confidence_claim_check": lambda: check_confidence_claims(evidence_path, transcript_path, high_threshold, heuristic_cap),
        "scope_completeness_check": lambda: check_scope_completeness(evidence_path),
        "heuristic_extraction_check": lambda: check_heuristic_extraction(evidence_path, transcript_path),
        "changed_files_summary_check": lambda: _check_changed_files_summary(evidence_path),
        "closeout_transcript_crosscheck": lambda: check_closeout_vs_transcript(evidence_path, transcript_path),
        "evidence_inventory_integrity": lambda: check_inventory_vs_transcript(evidence_path, transcript_path),
        "evidence_validation_integrity": lambda: check_closeout_vs_validation(evidence_path, transcript_path),
        "evidence_depth": lambda: check_evidence_depth(evidence_path),
        "git_status_authenticity": lambda: check_git_status_authenticity(evidence_path, transcript_path),
    }

    for check_name in required_checks:
        if check_name in check_registry:
            try:
                result = check_registry[check_name]()
                results.append(result)
            except Exception as exc:
                results.append(CheckResult(
                    check_name=check_name,
                    status="error",
                    issues=[Issue(check_name, Severity.BLOCKER, f"Check failed: {exc}")],
                ))
        else:
            results.append(CheckResult(
                check_name=check_name,
                status="skipped",
                issues=[Issue(check_name, Severity.WARNING, f"Unknown check: {check_name}")],
            ))

    return results


def _check_deterministic_artifact(evidence_path: str) -> CheckResult:
    path = Path(evidence_path)
    issues: list[Issue] = []
    inventory_path = path / "artifacts" / "inventory.json"
    findings_path = path / "artifacts" / "extracted_findings.json"

    if inventory_path.is_file():
        try:
            data = json.loads(inventory_path.read_text(encoding="utf-8"))
            if not data:
                issues.append(Issue("deterministic_evidence_artifact", Severity.BLOCKER,
                                    "inventory.json is empty"))
        except json.JSONDecodeError:
            issues.append(Issue("deterministic_evidence_artifact", Severity.BLOCKER,
                                "inventory.json is not valid JSON"))
    else:
        issues.append(Issue("deterministic_evidence_artifact", Severity.BLOCKER,
                            "Missing artifacts/inventory.json"))

    if findings_path.is_file():
        try:
            data = json.loads(findings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("placeholder") or not data:
                issues.append(Issue("deterministic_evidence_artifact", Severity.BLOCKER,
                                    "extracted_findings.json appears to be a placeholder"))
        except json.JSONDecodeError:
            issues.append(Issue("deterministic_evidence_artifact", Severity.BLOCKER,
                                "extracted_findings.json is not valid JSON"))

    status = "passed" if not any(i.severity == Severity.BLOCKER for i in issues) else "blocked"
    return CheckResult(check_name="deterministic_evidence_artifact", status=status, issues=issues)


def _check_no_repo_modification(repo_path: str, task_type: str, evidence_path: str = "") -> CheckResult:
    if task_type not in {"audit", "review", "listing", "evaluation", "signoff"}:
        return CheckResult(check_name="no_repo_modification_unless_requested", status="passed")

    ev_dir = evidence_path or repo_path
    status_path = Path(ev_dir) / "workspace" / "git_status_after.txt"
    if not status_path.is_file():
        alt_path = Path(repo_path) / "evidence" / "workspace" / "git_status_after.txt"
        if not alt_path.is_file():
            return CheckResult(check_name="no_repo_modification_unless_requested", status="passed")
        status_path = alt_path

    content = status_path.read_text(encoding="utf-8", errors="replace")
    if content.strip():
        return CheckResult(
            check_name="no_repo_modification_unless_requested",
            status="failed",
            issues=[Issue("no_repo_modification_unless_requested", Severity.REJECT,
                          "Target repo was modified during a read-only task")],
        )
    return CheckResult(check_name="no_repo_modification_unless_requested", status="passed")


def _check_changed_files_summary(evidence_path: str) -> CheckResult:
    path = Path(evidence_path)
    changed_path = path / "changed_files.txt"
    if changed_path.is_file():
        content = changed_path.read_text(encoding="utf-8", errors="replace").strip()
        if not content:
            return CheckResult(
                check_name="changed_files_summary_check",
                status="failed",
                issues=[Issue("changed_files_summary_check", Severity.REJECT,
                              "changed_files.txt exists but is empty")],
            )
        return CheckResult(check_name="changed_files_summary_check", status="passed")
    return CheckResult(
        check_name="changed_files_summary_check",
        status="blocked",
        issues=[Issue("changed_files_summary_check", Severity.BLOCKER,
                      "Missing changed_files.txt")],
    )


def compute_decision(results: list[CheckResult], policy: dict) -> Decision:
    reject_conditions = policy.get("reject_if", [])
    block_conditions = policy.get("block_if", [])

    has_blocker = False
    has_reject = False
    has_warning = False

    for result in results:
        for issue in result.issues:
            if issue.severity == Severity.BLOCKER:
                has_blocker = True
            elif issue.severity == Severity.REJECT:
                has_reject = True
            elif issue.severity == Severity.WARNING:
                has_warning = True

        if result.status == "blocked":
            has_blocker = True
        elif result.status == "failed":
            has_reject = True

    if has_blocker:
        return Decision.BLOCKED
    if has_reject:
        return Decision.REJECT
    if has_warning:
        return Decision.ACCEPT_WITH_CONDITIONS
    return Decision.ACCEPT


def generate_report(
    decision: Decision,
    task_type: str,
    policy: dict,
    repo_path: str,
    evidence_path: str,
    transcript_path: str,
    results: list[CheckResult],
) -> AcceptanceReport:
    failed_checks = [r.check_name for r in results if r.status == "failed"]
    blocked_checks = [r.check_name for r in results if r.status == "blocked"]
    warning_checks = [r.check_name for r in results if r.status in ("warning", "passed") and any(
        i.severity == Severity.WARNING for i in r.issues)]

    next_action = "No action required."
    if decision == Decision.BLOCKED:
        next_action = "Provide missing evidence and rerun acceptance checks."
    elif decision == Decision.REJECT:
        next_action = "Address the failed checks listed above, regenerate evidence, and resubmit."
    elif decision == Decision.ACCEPT_WITH_CONDITIONS:
        next_action = "Address warnings before proceeding to the next stage."

    return AcceptanceReport(
        decision=decision,
        task_type=task_type,
        policy_name=policy.get("name", "unknown"),
        repo_path=repo_path,
        evidence_path=evidence_path,
        transcript_path=transcript_path,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        checks=results,
        failed_checks=failed_checks,
        blocked_checks=blocked_checks,
        warning_checks=warning_checks,
        required_next_action=next_action,
    )


def run_acceptance(
    repo_path: str,
    evidence_path: str,
    transcript_path: str,
    policy_dir: str,
    policy_name: str | None = None,
    task_type: str | None = None,
    scratch_dir: str = "",
) -> AcceptanceReport:
    if task_type is None:
        task_type = "audit"

    policy, resolved_name = resolve_policy(task_type, policy_dir, policy_name)
    risk_tier = classify_risk(task_type, policy)
    results = evaluate_checks(repo_path, evidence_path, transcript_path, policy, task_type, scratch_dir)
    decision = compute_decision(results, policy)

    report = generate_report(
        decision, task_type, policy, repo_path, evidence_path, transcript_path, results
    )

    if risk_tier == "high" and decision == Decision.ACCEPT:
        report.decision = Decision.ACCEPT_WITH_CONDITIONS
        report.required_next_action = "High-risk task requires human sign-off before final acceptance."

    return report
