from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CODE_CHANGE_TASK_TYPES = {"code_change", "bugfix", "feature", "refactor", "dependency_update"}
VALIDATION_MISSING = "AI_GATE_VALIDATION_MISSING"
VALIDATION_TIMEOUT = "AI_GATE_VALIDATION_TIMEOUT"
VALIDATION_FAILED = "AI_GATE_VALIDATION_FAILED"
VALIDATION_PASSED = "AI_GATE_VALIDATION_PASSED"

DESTRUCTIVE_TOKENS = (
    "deploy",
    "publish",
    "unpublish",
    "migrate",
    "migration",
    "seed",
    "clean",
    "install",
    "uninstall",
    "release",
    "rm -rf",
    "rmdir",
    "del /",
    "format ",
    "drop table",
    "drop database",
    "kubectl",
    "terraform apply",
    "docker compose down",
)


@dataclass
class ValidationCommand:
    kind: str
    command: list[str]
    source: str


@dataclass
class ValidationResult:
    kind: str
    command: list[str] | None
    status: str
    exit_code: int | None
    output: str
    timed_out: bool = False
    skipped_reason: str = ""


def _is_destructive_text(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in DESTRUCTIVE_TOKENS)


def _load_package_scripts(project_dir: Path) -> dict[str, str]:
    package_path = project_dir / "package.json"
    if not package_path.is_file():
        return {}
    try:
        data = json.loads(package_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    scripts = data.get("scripts") or {}
    if not isinstance(scripts, dict):
        return {}
    return {str(k): str(v) for k, v in scripts.items()}


def _safe_script(project_dir: Path, name: str, scripts: dict[str, str]) -> ValidationCommand | None:
    body = scripts.get(name)
    if body is None:
        return None
    if _is_destructive_text(name) or _is_destructive_text(body):
        return None
    return ValidationCommand(kind=name, command=["npm", "run", name], source="package.json")


def detect_validation_commands(project_dir: str) -> dict[str, ValidationCommand]:
    """Auto-detect conservative validation commands only."""
    root = Path(project_dir)
    commands: dict[str, ValidationCommand] = {}

    has_python_project = any((root / name).exists() for name in (
        "pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", "tox.ini",
    ))
    if has_python_project and (root / "tests").is_dir():
        commands["test"] = ValidationCommand(
            kind="test",
            command=[sys.executable, "-m", "pytest", "tests/", "-q"],
            source="python-tests-dir",
        )

    scripts = _load_package_scripts(root)
    node_test = _safe_script(root, "test", scripts)
    if node_test and "test" not in commands:
        commands["test"] = node_test

    lint = _safe_script(root, "lint", scripts)
    if lint:
        commands["lint"] = lint

    typecheck = _safe_script(root, "typecheck", scripts)
    if typecheck:
        commands["typecheck"] = typecheck

    return commands


def _run_command(project_dir: str, cmd: ValidationCommand, timeout_seconds: int) -> ValidationResult:
    try:
        proc = subprocess.run(
            cmd.command,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        output = (
            f"{VALIDATION_TIMEOUT}: validation timed out after {timeout_seconds}s\n"
            f"Command: {' '.join(cmd.command)}\n"
        )
        if exc.stdout:
            output += f"\nSTDOUT before timeout:\n{exc.stdout}\n"
        if exc.stderr:
            output += f"\nSTDERR before timeout:\n{exc.stderr}\n"
        return ValidationResult(
            kind=cmd.kind,
            command=cmd.command,
            status="timeout",
            exit_code=None,
            output=output,
            timed_out=True,
        )
    except OSError as exc:
        return ValidationResult(
            kind=cmd.kind,
            command=cmd.command,
            status="failed",
            exit_code=None,
            output=f"{VALIDATION_FAILED}: validation command could not start: {exc}\n",
        )

    marker = VALIDATION_PASSED if proc.returncode == 0 else VALIDATION_FAILED
    status = "passed" if proc.returncode == 0 else "failed"
    output = (
        f"{marker}: exit_code={proc.returncode}\n"
        f"Command: {' '.join(cmd.command)}\n\n"
        f"STDOUT:\n{proc.stdout or ''}\n\n"
        f"STDERR:\n{proc.stderr or ''}\n"
    )
    return ValidationResult(
        kind=cmd.kind,
        command=cmd.command,
        status=status,
        exit_code=proc.returncode,
        output=output,
    )


def _missing(kind: str) -> ValidationResult:
    return ValidationResult(
        kind=kind,
        command=None,
        status="missing",
        exit_code=None,
        output=(
            f"{VALIDATION_MISSING}: no conservative {kind} command was auto-detected.\n"
            "Acceptance for code-change task types must remain BLOCKED until validation evidence exists.\n"
        ),
        skipped_reason="not auto-detected",
    )


def run_validation(
    project_dir: str,
    validation_dir: str,
    task_type: str,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Run conservative validation and write evidence outputs."""
    out_dir = Path(validation_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    commands = detect_validation_commands(project_dir)
    results: dict[str, ValidationResult] = {}

    for kind in ("test", "lint", "typecheck"):
        cmd = commands.get(kind)
        if task_type not in CODE_CHANGE_TASK_TYPES:
            results[kind] = ValidationResult(
                kind=kind,
                command=None,
                status="not_required",
                exit_code=None,
                output=(
                    f"Validation not required for task_type={task_type}.\n"
                    "No code-change validation command was run by the daemon.\n"
                ),
            )
        elif cmd is None:
            results[kind] = _missing(kind)
        else:
            results[kind] = _run_command(project_dir, cmd, timeout_seconds)

    file_map = {
        "test": "test_output.txt",
        "lint": "lint_output.txt",
        "typecheck": "typecheck_output.txt",
    }
    for kind, filename in file_map.items():
        (out_dir / filename).write_text(results[kind].output, encoding="utf-8")

    summary_lines = ["# Validation Summary", ""]
    terminal_status = "passed"
    for kind in ("test", "lint", "typecheck"):
        result = results[kind]
        command_text = " ".join(result.command) if result.command else "(none)"
        summary_lines.append(f"- {kind}: {result.status}; command: `{command_text}`")
        if result.status in {"missing", "timeout"}:
            terminal_status = "blocked"
        elif result.status == "failed" and terminal_status != "blocked":
            terminal_status = "failed"
    summary_lines.append("")
    summary_lines.append(f"Overall validation status: {terminal_status}")
    (out_dir.parent / "validation_summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    return {
        "status": terminal_status,
        "commands_detected": {k: v.command for k, v in commands.items()},
        "results": {
            k: {
                "status": v.status,
                "exit_code": v.exit_code,
                "timed_out": v.timed_out,
                "skipped_reason": v.skipped_reason,
                "command": v.command,
            }
            for k, v in results.items()
        },
    }
