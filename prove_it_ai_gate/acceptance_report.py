from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .gate_engine import AcceptanceReport
from .types import Decision, Severity, CheckResult


def format_markdown_report(report: AcceptanceReport) -> str:
    lines = [
        "# Acceptance Report",
        "",
        f"**Decision:** `{report.decision.value}`",
        "",
        f"- Policy: `{report.policy_name}`",
        f"- Task type: `{report.task_type}`",
        f"- Repo: `{report.repo_path}`",
        f"- Evidence: `{report.evidence_path}`",
        f"- Transcript: `{report.transcript_path}`",
        f"- Generated: {report.generated_at}",
        "",
        "---",
        "",
    ]

    if report.decision != Decision.ACCEPT:
        lines.append("## Why")
        lines.append("")
        if report.blocked_checks:
            lines.append("### Blocked Checks")
            for name in report.blocked_checks:
                lines.append(f"- `{name}`")
            lines.append("")
        if report.failed_checks:
            lines.append("### Failed Checks")
            for name in report.failed_checks:
                lines.append(f"- `{name}`")
            lines.append("")
        if report.warning_checks:
            lines.append("### Checks With Warnings")
            for name in report.warning_checks:
                lines.append(f"- `{name}`")
            lines.append("")

    lines.append("## Check Results")
    lines.append("")
    lines.append(_check_results_table(report.checks))
    lines.append("")

    lines.append("## Issues Detail")
    lines.append("")
    all_issues: list[dict[str, Any]] = []
    for result in report.checks:
        for issue in result.issues:
            all_issues.append({
                "check": result.check_name,
                "severity": issue.severity.value,
                "message": issue.message,
            })

    if all_issues:
        lines.append("| Check | Severity | Issue |")
        lines.append("|---|---|---|")
        for iss in all_issues:
            sev = f"`{iss['severity']}`"
            lines.append(f"| `{iss['check']}` | {sev} | {iss['message']} |")
    else:
        lines.append("_No issues found._")
    lines.append("")

    lines.append("## Required Next Action")
    lines.append("")
    lines.append(report.required_next_action)
    lines.append("")

    return "\n".join(lines)


def _check_results_table(checks: list) -> str:
    headers = ["Check", "Status", "Issues"]
    rows: list[list[str]] = []
    for check in checks:
        blocker_count = sum(1 for i in check.issues if i.severity == Severity.BLOCKER)
        reject_count = sum(1 for i in check.issues if i.severity == Severity.REJECT)
        warning_count = sum(1 for i in check.issues if i.severity == Severity.WARNING)
        counts_parts: list[str] = []
        if blocker_count:
            counts_parts.append(f"{blocker_count} blocker(s)")
        if reject_count:
            counts_parts.append(f"{reject_count} reject(s)")
        if warning_count:
            counts_parts.append(f"{warning_count} warning(s)")
        counts = ", ".join(counts_parts) if counts_parts else "0"
        rows.append([f"`{check.check_name}`", f"`{check.status}`", counts])

    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def format_json_report(report: AcceptanceReport) -> dict:
    checks_data: list[dict] = []
    for result in report.checks:
        issues_data: list[dict] = []
        for issue in result.issues:
            issues_data.append({
                "severity": issue.severity.value,
                "message": issue.message,
                "evidence": issue.evidence,
            })
        checks_data.append({
            "check_name": result.check_name,
            "status": result.status,
            "issues": issues_data,
            "details": result.details,
        })

    return {
        "decision": report.decision.value,
        "policy_name": report.policy_name,
        "task_type": report.task_type,
        "repo_path": report.repo_path,
        "evidence_path": report.evidence_path,
        "transcript_path": report.transcript_path,
        "generated_at": report.generated_at,
        "checks": checks_data,
        "summary": {
            "total_checks": len(report.checks),
            "passed": sum(1 for c in report.checks if c.status == "passed"),
            "blocked": sum(1 for c in report.checks if c.status == "blocked"),
            "failed": sum(1 for c in report.checks if c.status == "failed"),
            "warning": sum(1 for c in report.checks if c.status == "warning"),
            "error": sum(1 for c in report.checks if c.status == "error"),
            "skipped": sum(1 for c in report.checks if c.status == "skipped"),
            "failed_checks": report.failed_checks,
            "blocked_checks": report.blocked_checks,
            "warning_checks": report.warning_checks,
        },
        "required_next_action": report.required_next_action,
    }


def write_report(report: AcceptanceReport, output_dir: str) -> tuple[Path, Path]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    md_path = out_dir / f"acceptance_report_{timestamp}.md"
    md_path.write_text(format_markdown_report(report), encoding="utf-8")

    json_path = out_dir / f"acceptance_report_{timestamp}.json"
    json_path.write_text(
        json.dumps(format_json_report(report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return md_path, json_path


def write_csv_report(report: AcceptanceReport, output_dir: str) -> Path:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    csv_path = out_dir / f"acceptance_report_{timestamp}.csv"

    rows: list[dict] = []
    for result in report.checks:
        for issue in result.issues:
            rows.append({
                "check_name": result.check_name,
                "check_status": result.status,
                "severity": issue.severity.value,
                "message": issue.message,
            })
        if not result.issues:
            rows.append({
                "check_name": result.check_name,
                "check_status": result.status,
                "severity": "",
                "message": "",
            })

    fieldnames = ["check_name", "check_status", "severity", "message"]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return csv_path
