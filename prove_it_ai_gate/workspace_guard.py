from __future__ import annotations

import subprocess
from pathlib import Path

from .types import CheckResult, Issue, Severity


def run_git_status(repo_path: str) -> tuple[str, str]:
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=30,
        )
        return result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return "", "git: command not found"
    except subprocess.TimeoutExpired:
        return "", "git: command timed out"
    except Exception as exc:
        return "", f"git: {exc}"


def parse_status_entries(status_output: str) -> dict[str, list[str]]:
    entries: dict[str, list[str]] = {
        "modified": [],
        "added": [],
        "deleted": [],
        "untracked": [],
        "renamed": [],
        "other": [],
    }
    if not status_output:
        return entries

    for line in status_output.splitlines():
        line = line.rstrip()
        if len(line) < 3:
            continue
        status_code = line[:2].strip()
        filepath = line[3:].strip()

        if status_code == "M":
            entries["modified"].append(filepath)
        elif status_code == "A":
            entries["added"].append(filepath)
        elif status_code == "D":
            entries["deleted"].append(filepath)
        elif status_code == "??":
            entries["untracked"].append(filepath)
        elif status_code.startswith("R"):
            entries["renamed"].append(filepath)
        else:
            entries["other"].append(f"{status_code}:{filepath}")

    return entries


def _read_status_file(evidence_path: str, repo_path: str, filename: str) -> str:
    status_path = Path(evidence_path) / "workspace" / filename
    if status_path.is_file():
        return status_path.read_text(encoding="utf-8", errors="replace").strip()
    alt_path = Path(repo_path) / "evidence" / "workspace" / filename
    if alt_path.is_file():
        return alt_path.read_text(encoding="utf-8", errors="replace").strip()
    return ""


def _detect_worktree_changes(status_before: str, status_after: str) -> bool:
    if status_before == "(missing)" or status_before == "":
        return status_after.strip() != "" and status_after != "(missing)"
    before_empty = status_before.strip() in ("", "(empty)", "(clean)")
    after_dirty = status_after.strip() not in ("", "(empty)", "(clean)", "(missing)")
    return before_empty and after_dirty


def _check_scratch_files(repo_path: str, status_after: str, scratch_dir: str = "") -> list[Issue]:
    issues: list[Issue] = []
    entries = parse_status_entries(status_after)

    scratch_patterns = [
        "scratch_", "tmp_", "temp_", "_scratch", "_tmp",
        ".py", ".ps1", ".sh", ".bat",
    ]

    allowed_prefix = ""
    if scratch_dir:
        scratch_dir_abs = str(Path(scratch_dir).resolve())
        allowed_prefix = scratch_dir_abs

    for filepath in entries["added"] + entries["untracked"]:
        lower = filepath.lower()
        if any(pattern in lower for pattern in scratch_patterns):
            if allowed_prefix:
                full_path = str((Path(repo_path) / filepath).resolve())
                if full_path.startswith(allowed_prefix):
                    continue
            issues.append(Issue(
                "workspace_hygiene_check",
                Severity.WARNING,
                f"Scratch file created in target repo: {filepath}",
            ))

    return issues


def check_workspace_hygiene(repo_path: str, task_type: str, evidence_path: str = "", scratch_dir: str = "") -> CheckResult:
    issues: list[Issue] = []
    is_read_only = task_type in {"audit", "review", "listing", "evaluation", "signoff"}

    ev_path = evidence_path or repo_path
    status_before = _read_status_file(ev_path, repo_path, "git_status_before.txt")
    status_after = _read_status_file(ev_path, repo_path, "git_status_after.txt")

    if not status_before and not status_after:
        current_status, git_err = run_git_status(repo_path)
        if git_err:
            issues.append(Issue(
                "workspace_hygiene_check",
                Severity.WARNING,
                f"Could not run git status: {git_err}",
            ))
            return CheckResult(
                check_name="workspace_hygiene_check",
                status="warning",
                issues=issues,
                details={"note": "git status unavailable; cannot verify workspace state"},
            )

        issues.append(Issue(
            "workspace_hygiene_check",
            Severity.WARNING,
            "Using live git status; evidence folder did not contain git_status_before.txt or git_status_after.txt",
        ))

        if current_status:
            if is_read_only:
                issues.append(Issue(
                    "workspace_hygiene_check",
                    Severity.REJECT,
                    f"Target repo has uncommitted changes during a read-only task",
                ))
            else:
                issues.append(Issue(
                    "workspace_hygiene_check",
                    Severity.WARNING,
                    "Target repo has uncommitted changes; verify these are the intended changes",
                ))
                issues.extend(_check_scratch_files(repo_path, current_status, scratch_dir))

        return CheckResult(
            check_name="workspace_hygiene_check",
            status="failed" if any(i.severity == Severity.REJECT for i in issues) else (
                "warning" if issues else "passed"
            ),
            issues=issues,
            details={"status_before": "(live)", "status_after": current_status, "using_live_git": True},
        )

    raw_before = status_before.strip()
    raw_after = status_after.strip()

    display_before = status_before or "(empty)"
    display_after = status_after or "(empty)"

    if is_read_only and _detect_worktree_changes(display_before, display_after):
        issues.append(Issue(
            "workspace_hygiene_check",
            Severity.REJECT,
            "Target repo was modified during a read-only task",
        ))
    elif not is_read_only and _detect_worktree_changes(display_before, display_after):
        issues.extend(_check_scratch_files(repo_path, display_after, scratch_dir))

    if is_read_only:
        closeout_path = Path(ev_path) / "closeout.md"
        if closeout_path.is_file():
            closeout = closeout_path.read_text(encoding="utf-8", errors="replace").lower()
            no_changes_claimed = any(phrase in closeout for phrase in [
                "no files changed", "no changes", "no modifications",
                "no files were modified", "did not modify",
                "no workspace changes",
            ])
            if no_changes_claimed and raw_after:
                issues.append(Issue(
                    "workspace_hygiene_check",
                    Severity.REJECT,
                    "Closeout claims no changes but git status shows modifications",
                ))

    status = "passed"
    if any(i.severity == Severity.REJECT for i in issues):
        status = "failed"
    elif any(i.severity == Severity.WARNING for i in issues):
        status = "warning"

    return CheckResult(
        check_name="workspace_hygiene_check",
        status=status,
        issues=issues,
        details={
            "status_before": display_before,
            "status_after": display_after,
            "is_read_only": is_read_only,
        },
    )
