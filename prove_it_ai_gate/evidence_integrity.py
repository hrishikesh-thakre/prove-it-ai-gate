from __future__ import annotations

import json
import re
from pathlib import Path

from .types import CheckResult, Issue, Severity


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return None


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _extract_numbers_from_text(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"\b(\d{1,6})\b", text)]


def _extract_tool_outputs(transcript_path: str) -> list[dict]:
    path = Path(transcript_path)
    if not path.is_file():
        return []
    outputs: list[dict] = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "tool_result" or event.get("role") == "tool":
                outputs.append(event)
    return outputs


def _find_tool_output(outputs: list[dict], tool_name: str, command_substring: str = "") -> list[str]:
    texts: list[str] = []
    for out in outputs:
        name_match = out.get("tool_name", "") == tool_name
        cmd_match = True
        if command_substring:
            cmd = out.get("command", "")
            cmd_match = command_substring.lower() in cmd.lower()
        if name_match and cmd_match:
            for field in ("stdout", "content", "output", "result"):
                val = out.get(field)
                if isinstance(val, str) and val.strip():
                    texts.append(val)
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, str):
                            texts.append(item)
    return texts


def check_inventory_vs_transcript(evidence_path: str, transcript_path: str) -> CheckResult:
    """Verify inventory.json counts match transcript tool outputs."""
    issues: list[Issue] = []

    inventory = _read_json(Path(evidence_path) / "artifacts" / "inventory.json")
    if not inventory:
        return CheckResult(
            check_name="evidence_inventory_integrity",
            status="warning",
            issues=[Issue("evidence_inventory_integrity", Severity.WARNING,
                          "No inventory.json found for integrity check")],
        )

    outputs = _extract_tool_outputs(transcript_path)
    if not outputs:
        return CheckResult(
            check_name="evidence_inventory_integrity",
            status="warning",
            issues=[Issue("evidence_inventory_integrity", Severity.WARNING,
                          "No transcript tool outputs found for integrity check")],
        )

    inventory_count = inventory.get("total_files") or len(inventory.get("files", []))

    tool_texts = _find_tool_output(outputs, "shell", "") + _find_tool_output(outputs, "bash", "")
    all_text = "\n".join(tool_texts)

    transcript_numbers = _extract_numbers_from_text(all_text)

    if inventory_count and transcript_numbers:
        max_transcript_count = max(transcript_numbers)
        if inventory_count > max_transcript_count * 1.2 and inventory_count > 100:
            issues.append(Issue(
                "evidence_inventory_integrity",
                Severity.REJECT,
                f"Inventory claims {inventory_count} files but largest transcript count is {max_transcript_count} "
                f"— possible evidence inflation",
            ))
        elif inventory_count > max_transcript_count and inventory_count > 50:
            issues.append(Issue(
                "evidence_inventory_integrity",
                Severity.WARNING,
                f"Inventory count ({inventory_count}) exceeds transcript counts ({max_transcript_count})",
            ))

    inventory_files = inventory.get("files", [])
    if inventory_files:
        sample_paths = set(str(f.get("path", "")) for f in inventory_files[:5])
        for sample in sample_paths:
            if sample and sample not in all_text:
                pass

    status = "passed"
    if any(i.severity == Severity.REJECT for i in issues):
        status = "failed"
    elif any(i.severity == Severity.WARNING for i in issues):
        status = "warning"

    return CheckResult(
        check_name="evidence_inventory_integrity",
        status=status,
        issues=issues,
        details={
            "inventory_count": inventory_count,
            "transcript_max_count": max(transcript_numbers) if transcript_numbers else None,
        },
    )


def check_closeout_vs_validation(evidence_path: str, transcript_path: str) -> CheckResult:
    """Verify closeout claims match validation outputs (test results, lint results)."""
    issues: list[Issue] = []

    closeout = _read_text(Path(evidence_path) / "closeout.md").lower()
    test_output = _read_text(Path(evidence_path) / "validation" / "test_output.txt")
    lint_output = _read_text(Path(evidence_path) / "validation" / "lint_output.txt")

    if not closeout:
        return CheckResult(
            check_name="evidence_validation_integrity",
            status="passed",
            issues=[],
        )

    if test_output:
        test_failures = re.findall(r"(\d+)\s+failed", test_output, re.IGNORECASE)
        if test_failures:
            failed_count = sum(int(f) for f in test_failures)
            if failed_count > 0:
                tests_passed_claims = ["tests pass", "tests passing", "all tests pass",
                                       "tests succeeded", "no test failures"]
                if any(phrase in closeout for phrase in tests_passed_claims):
                    issues.append(Issue(
                        "evidence_validation_integrity",
                        Severity.REJECT,
                        f"Closeout claims tests passed but test output shows {failed_count} failure(s)",
                    ))

    if lint_output:
        lint_issues = re.findall(r"(\d+)\s+(?:error|issue|warning|problem)", lint_output, re.IGNORECASE)
        if lint_issues:
            issue_count = sum(int(i) for i in lint_issues)
            if issue_count > 0:
                lint_clean_claims = ["no lint", "lint passed", "no issues", "all checks passed",
                                     "no errors found"]
                if any(phrase in closeout for phrase in lint_clean_claims):
                    issues.append(Issue(
                        "evidence_validation_integrity",
                        Severity.REJECT,
                        f"Closeout claims clean lint but lint output shows {issue_count} issue(s)",
                    ))

    outputs = _extract_tool_outputs(transcript_path)
    if test_output and outputs:
        tool_texts = _find_tool_output(outputs, "shell", "test") + _find_tool_output(outputs, "shell", "pytest")
        all_tool_text = "\n".join(tool_texts)
        if all_tool_text:
            tool_failures = re.findall(r"(\d+)\s+failed", all_tool_text, re.IGNORECASE)
            test_failures = re.findall(r"(\d+)\s+failed", test_output, re.IGNORECASE)
            if tool_failures and test_failures:
                tool_failed = sum(int(f) for f in tool_failures)
                evidence_failed = sum(int(f) for f in test_failures)
                if (tool_failed > 0 and evidence_failed == 0) or (tool_failed == 0 and evidence_failed > 0):
                    issues.append(Issue(
                        "evidence_validation_integrity",
                        Severity.REJECT,
                        f"Test output mismatch: transcript shows {tool_failed} failures, "
                        f"evidence shows {evidence_failed} failures",
                    ))

    status = "passed"
    if any(i.severity == Severity.REJECT for i in issues):
        status = "failed"
    elif any(i.severity == Severity.WARNING for i in issues):
        status = "warning"

    return CheckResult(
        check_name="evidence_validation_integrity",
        status=status,
        issues=issues,
        details={"closeout_checked": bool(closeout), "test_output_checked": bool(test_output),
                 "lint_output_checked": bool(lint_output)},
    )


def check_timestamp_consistency(evidence_path: str) -> CheckResult:
    """Verify evidence file timestamps are consistent with each other."""
    issues: list[Issue] = []

    evidence_root = Path(evidence_path)
    if not evidence_root.is_dir():
        return CheckResult(check_name="timestamp_consistency", status="passed")

    closeout = evidence_root / "closeout.md"
    inventory = evidence_root / "artifacts" / "inventory.json"
    findings = evidence_root / "artifacts" / "extracted_findings.json"

    timestamps: dict[str, float] = {}
    for label, path in [("closeout", closeout), ("inventory", inventory), ("findings", findings)]:
        if path.is_file():
            timestamps[label] = path.stat().st_mtime

    if "closeout" in timestamps and "inventory" in timestamps:
        if timestamps["closeout"] < timestamps["inventory"]:
            pass
        else:
            delta = timestamps["closeout"] - timestamps["inventory"]
            if delta < 0.1:
                issues.append(Issue(
                    "timestamp_consistency",
                    Severity.WARNING,
                    "Closeout and inventory have nearly identical timestamps — may be auto-generated in bulk",
                ))

    return CheckResult(
        check_name="timestamp_consistency",
        status="warning" if issues else "passed",
        issues=issues,
        details={"timestamps_found": len(timestamps)},
    )


def check_evidence_depth(evidence_path: str) -> CheckResult:
    """Verify evidence files have meaningful depth, not just surface compliance."""
    issues: list[Issue] = []

    evidence_root = Path(evidence_path)

    inventory = _read_json(evidence_root / "artifacts" / "inventory.json")
    if inventory:
        files = inventory.get("files", [])
        if isinstance(files, list) and len(files) > 0:
            has_sizes = sum(1 for f in files if isinstance(f, dict) and f.get("size_bytes", 0) > 0)
            if has_sizes < len(files) * 0.5:
                issues.append(Issue(
                    "evidence_depth",
                    Severity.WARNING,
                    f"Less than 50% of inventory entries have size_bytes — inventory may be superficial",
                ))

            sample_paths = [str(f.get("path", "")) for f in files[:3]]
            if all(len(p) < 10 for p in sample_paths):
                issues.append(Issue(
                    "evidence_depth",
                    Severity.WARNING,
                    "Inventory file paths appear truncated or generic — may be auto-generated stub",
                ))

    findings = _read_json(evidence_root / "artifacts" / "extracted_findings.json")
    if findings:
        meaningful_keys = sum(1 for k in findings if k not in ("placeholder", "note", "status", "generated_at"))
        if meaningful_keys == 0:
            issues.append(Issue(
                "evidence_depth",
                Severity.BLOCKER,
                "extracted_findings.json contains only metadata keys, no actual findings",
            ))

    closeout = _read_text(evidence_root / "closeout.md")
    if closeout:
        lines = [l for l in closeout.splitlines() if l.strip() and not l.strip().startswith("#")]
        if len(lines) < 3:
            issues.append(Issue(
                "evidence_depth",
                Severity.WARNING,
                "Closeout has fewer than 3 substantive lines — may be a stub",
            ))

    risks = _read_text(evidence_root / "risks.md")
    if risks:
        if "low risk" in risks.lower() and len(risks) < 100:
            issues.append(Issue(
                "evidence_depth",
                Severity.WARNING,
                "Risks section claims 'low risk' with minimal detail — may be superficial",
            ))

    status = "passed"
    if any(i.severity == Severity.BLOCKER for i in issues):
        status = "blocked"
    elif any(i.severity == Severity.REJECT for i in issues):
        status = "failed"
    elif any(i.severity == Severity.WARNING for i in issues):
        status = "warning"

    return CheckResult(
        check_name="evidence_depth",
        status=status,
        issues=issues,
        details={},
    )


def check_git_status_authenticity(evidence_path: str, transcript_path: str) -> CheckResult:
    """Verify git status files are consistent with transcript git commands."""
    issues: list[Issue] = []

    status_before = _read_text(Path(evidence_path) / "workspace" / "git_status_before.txt")
    status_after = _read_text(Path(evidence_path) / "workspace" / "git_status_after.txt")

    if not status_before and not status_after:
        return CheckResult(check_name="git_status_authenticity", status="passed")

    outputs = _extract_tool_outputs(transcript_path)
    git_outputs = _find_tool_output(outputs, "shell", "git status")

    if status_after.strip() and git_outputs:
        after_lines = set(status_after.strip().splitlines())
        transcript_lines: set[str] = set()
        for out in git_outputs:
            for line in out.splitlines():
                line = line.strip()
                if line and not line.startswith("$") and not line.startswith(">"):
                    transcript_lines.add(line)

        if after_lines and transcript_lines:
            if not after_lines.intersection(transcript_lines):
                issues.append(Issue(
                    "git_status_authenticity",
                    Severity.REJECT,
                    "Git status in evidence does not match any git output in transcript — possible fabrication",
                ))

    status_before_path = Path(evidence_path) / "workspace" / "git_status_before.txt"
    status_after_path = Path(evidence_path) / "workspace" / "git_status_after.txt"

    if status_before_path.is_file() and status_after_path.is_file():
        b_time = status_before_path.stat().st_mtime
        a_time = status_after_path.stat().st_mtime
        if abs(b_time - a_time) < 0.5:
            issues.append(Issue(
                "git_status_authenticity",
                Severity.WARNING,
                "Git status before and after have nearly identical timestamps — may be fabricated together",
            ))

    status = "passed"
    if any(i.severity == Severity.REJECT for i in issues):
        status = "failed"
    elif any(i.severity == Severity.WARNING for i in issues):
        status = "warning"

    return CheckResult(
        check_name="git_status_authenticity",
        status=status,
        issues=issues,
        details={},
    )
