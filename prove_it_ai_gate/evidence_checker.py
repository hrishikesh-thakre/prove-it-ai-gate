from __future__ import annotations

import json
import os
from pathlib import Path

from .types import CheckResult, Issue, Severity

REQUIRED_EVIDENCE = {
    "audit": ["transcript", "scoped_corpus_definition", "inventory_or_partial_sample_warning",
               "deterministic_extraction_artifact", "workspace_status", "audit_guard_result",
               "confidence_limits"],
    "review": ["transcript", "scoped_corpus_definition", "inventory_or_partial_sample_warning",
                "deterministic_extraction_artifact", "workspace_status", "audit_guard_result",
                "confidence_limits"],
    "listing": ["transcript", "scoped_corpus_definition", "inventory_or_partial_sample_warning",
                 "deterministic_extraction_artifact", "workspace_status", "audit_guard_result",
                 "confidence_limits"],
    "evaluation": ["transcript", "scoped_corpus_definition", "inventory_or_partial_sample_warning",
                    "deterministic_extraction_artifact", "workspace_status", "audit_guard_result",
                    "confidence_limits"],
    "signoff": ["transcript", "scoped_corpus_definition", "inventory_or_partial_sample_warning",
                 "deterministic_extraction_artifact", "workspace_status", "audit_guard_result",
                 "confidence_limits"],
    "code_change": ["changed_files_list", "git_diff", "test_output", "lint_output",
                     "typecheck_output", "validation_summary", "risk_classification",
                     "unresolved_risks"],
    "bugfix": ["changed_files_list", "git_diff", "test_output", "lint_output",
                "typecheck_output", "validation_summary", "risk_classification",
                "unresolved_risks"],
    "feature": ["changed_files_list", "git_diff", "test_output", "lint_output",
                 "typecheck_output", "validation_summary", "risk_classification",
                 "unresolved_risks"],
    "refactor": ["changed_files_list", "git_diff", "test_output", "lint_output",
                  "typecheck_output", "validation_summary", "risk_classification",
                  "unresolved_risks"],
    "dependency_update": ["changed_files_list", "git_diff", "test_output", "lint_output",
                           "typecheck_output", "validation_summary", "risk_classification",
                           "unresolved_risks"],
    "corpus_audit": ["full_inventory", "extraction_script_or_command", "extracted_data_artifact",
                      "scope_statement", "heuristic_limitations", "audit_guard_result"],
    "inventory": ["full_inventory", "extraction_script_or_command", "extracted_data_artifact",
                   "scope_statement", "heuristic_limitations", "audit_guard_result"],
    "extraction": ["full_inventory", "extraction_script_or_command", "extracted_data_artifact",
                    "scope_statement", "heuristic_limitations", "audit_guard_result"],
    "data_audit": ["full_inventory", "extraction_script_or_command", "extracted_data_artifact",
                    "scope_statement", "heuristic_limitations", "audit_guard_result"],
    "compliance_scan": ["full_inventory", "extraction_script_or_command", "extracted_data_artifact",
                          "scope_statement", "heuristic_limitations", "audit_guard_result"],
}

EVIDENCE_FILE_MAP = {
    "transcript": "transcript.jsonl",
    "changed_files_list": "changed_files.txt",
    "git_diff": "workspace/diff_summary.patch",
    "test_output": "validation/test_output.txt",
    "lint_output": "validation/lint_output.txt",
    "typecheck_output": "validation/typecheck_output.txt",
    "validation_summary": "validation_summary.md",
    "risk_classification": "risks.md",
    "unresolved_risks": "risks.md",
    "workspace_status": "workspace/git_status_before.txt",
    "scoped_corpus_definition": "brief.md",
    "inventory_or_partial_sample_warning": "artifacts/inventory.json",
    "deterministic_extraction_artifact": "artifacts/extracted_findings.json",
    "audit_guard_result": "validation/audit_guard.txt",
    "confidence_limits": "risks.md",
    "full_inventory": "artifacts/inventory.json",
    "extraction_script_or_command": "commands.jsonl",
    "extracted_data_artifact": "artifacts/extracted_findings.json",
    "scope_statement": "brief.md",
    "heuristic_limitations": "risks.md",
    "closeout": "closeout.md",
}

PLACEHOLDER_PATTERNS = [
    "TODO", "FIXME", "placeholder", "TBD", "to be determined",
    "not implemented", "coming soon", "work in progress",
]

MIN_FILE_SIZES = {
    "changed_files.txt": 1,
    "validation/test_output.txt": 5,
    "validation/lint_output.txt": 5,
    "validation/typecheck_output.txt": 5,
}

SKIP_EMPTY_CHECK = {
    "workspace/git_status_before.txt",
    "workspace/git_status_after.txt",
}


def _check_file_non_empty(evidence_root: Path, rel_path: str) -> tuple[bool, str]:
    file_path = evidence_root / rel_path
    if not file_path.is_file():
        return False, f"Missing: {rel_path}"

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception as exc:
        return False, f"Cannot read {rel_path}: {exc}"

    if not content:
        if rel_path in SKIP_EMPTY_CHECK:
            return True, ""
        return False, f"Empty file: {rel_path}"

    min_size = MIN_FILE_SIZES.get(rel_path, 1)
    if len(content) < min_size:
        return False, f"File too small: {rel_path} ({len(content)} chars, min {min_size})"

    content_lower = content.lower()
    for pattern in PLACEHOLDER_PATTERNS:
        if pattern.lower() in content_lower and len(content) < 100:
            return False, f"Possible placeholder content in {rel_path} (contains '{pattern}')"

    return True, ""


def _check_truncation_in_evidence_file(file_path: Path) -> bool:
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        truncation_signs = [
            "truncated", "output omitted", "showing first",
            "output truncated", "... (truncated)",
        ]
        content_lower = content.lower()
        return any(sign.lower() in content_lower for sign in truncation_signs)
    except Exception:
        return False


def check_evidence_folder(evidence_path: str, task_type: str) -> CheckResult:
    evidence_root = Path(evidence_path)
    issues: list[Issue] = []
    required = REQUIRED_EVIDENCE.get(task_type, REQUIRED_EVIDENCE.get("audit", []))

    if not evidence_root.is_dir():
        return CheckResult(
            check_name="evidence_folder_schema_check",
            status="blocked",
            issues=[Issue("evidence_folder_schema_check", Severity.BLOCKER,
                          f"Evidence folder not found: {evidence_path}")],
        )

    for ev_key in required:
        rel_path = EVIDENCE_FILE_MAP.get(ev_key)
        if not rel_path:
            continue
        ok, msg = _check_file_non_empty(evidence_root, rel_path)
        if not ok:
            issues.append(Issue("evidence_folder_schema_check", Severity.BLOCKER, msg))

    for root, _dirs, files in os.walk(evidence_root):
        for fname in files:
            fpath = Path(root) / fname
            if _check_truncation_in_evidence_file(fpath):
                rel = fpath.relative_to(evidence_root)
                issues.append(Issue(
                    "evidence_folder_schema_check",
                    Severity.WARNING,
                    f"Evidence file may contain truncated content: {rel}",
                ))

    status = "passed" if not any(i.severity == Severity.BLOCKER for i in issues) else "blocked"
    return CheckResult(
        check_name="evidence_folder_schema_check",
        status=status,
        issues=issues,
        details={
            "evidence_root": str(evidence_root),
            "required_keys": required,
            "task_type": task_type,
        },
    )


def inventory_evidence_files(evidence_path: str) -> dict:
    evidence_root = Path(evidence_path)
    if not evidence_root.is_dir():
        return {"error": f"Not a directory: {evidence_path}", "files": []}

    files: list[dict] = []
    for entry in sorted(evidence_root.rglob("*")):
        if entry.is_file() and ".git" not in entry.parts:
            rel = str(entry.relative_to(evidence_root))
            stat = entry.stat()
            files.append({
                "path": rel,
                "size_bytes": stat.st_size,
            })
    return {"evidence_root": str(evidence_root), "file_count": len(files), "files": files}
