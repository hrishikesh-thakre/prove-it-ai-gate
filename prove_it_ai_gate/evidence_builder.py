from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .acceptance_report import write_csv_report, write_report
from .daemon_state import utc_now
from .gate_engine import run_acceptance
from .validation_runner import run_validation


DEFAULT_EXCLUDES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value or "unknown").strip("-")
    return cleaned[:80] or "unknown"


def make_run_id(agent: str, session_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{_safe_id(agent)}-{_safe_id(session_id)}"


def _run_git(project_dir: str, args: list[str], timeout: int = 20) -> tuple[int | None, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except Exception as exc:
        return None, "", str(exc)


def _relative_evidence_prefix(project_dir: str, evidence_root: str) -> str:
    try:
        root = Path(project_dir).resolve()
        evidence = Path(evidence_root).resolve()
        rel = evidence.relative_to(root)
    except (OSError, ValueError):
        return ""
    return str(rel).replace("\\", "/").rstrip("/") + "/"


def _filter_evidence_status(status: str, project_dir: str, evidence_root: str) -> str:
    prefix = _relative_evidence_prefix(project_dir, evidence_root)
    if not prefix:
        return status
    kept: list[str] = []
    for line in status.splitlines():
        if len(line) < 4:
            kept.append(line)
            continue
        path = line[3:].strip().replace("\\", "/")
        if path == prefix.rstrip("/") or path.startswith(prefix):
            continue
        kept.append(line)
    return "\n".join(kept)


def _git_status(project_dir: str, evidence_root: str) -> str:
    code, stdout, stderr = _run_git(project_dir, ["status", "--short"])
    if code == 0:
        filtered = _filter_evidence_status(stdout, project_dir, evidence_root).strip()
        return filtered or "(clean)"
    return f"(unavailable)\n{stderr.strip()}".strip()


def _git_diff(project_dir: str) -> str:
    code, stdout, stderr = _run_git(project_dir, ["diff", "--"])
    if code == 0:
        return stdout if stdout.strip() else "(no diff)\n"
    return f"(diff unavailable)\n{stderr.strip()}\n"


def _git_changed_files(project_dir: str, evidence_root: str = "") -> list[str]:
    code, stdout, _stderr = _run_git(project_dir, ["diff", "--name-only", "--"])
    changed = [line.strip() for line in stdout.splitlines() if line.strip()] if code == 0 else []
    code_status, status, _stderr_status = _run_git(project_dir, ["status", "--short"])
    if code_status == 0:
        for line in status.splitlines():
            if not line.strip():
                continue
            path = line[3:].strip()
            if path and path not in changed:
                changed.append(path)
    prefix = _relative_evidence_prefix(project_dir, evidence_root) if evidence_root else ""
    if prefix:
        changed = [
            item for item in changed
            if item.replace("\\", "/") != prefix.rstrip("/")
            and not item.replace("\\", "/").startswith(prefix)
        ]
    return sorted(set(changed))


def _inventory(project_dir: str, evidence_root: str) -> dict[str, Any]:
    root = Path(project_dir).resolve()
    evidence_path = Path(evidence_root).resolve()
    files: list[dict[str, Any]] = []

    for current, dirs, filenames in os.walk(root):
        cur_path = Path(current)
        dirs[:] = [
            d for d in dirs
            if d not in DEFAULT_EXCLUDES
            and not str((cur_path / d).resolve()).startswith(str(evidence_path))
        ]
        for filename in filenames:
            path = cur_path / filename
            try:
                resolved = path.resolve()
                if str(resolved).startswith(str(evidence_path)):
                    continue
                rel = resolved.relative_to(root)
                stat = resolved.stat()
            except (OSError, ValueError):
                continue
            files.append({
                "path": str(rel).replace("\\", "/"),
                "size_bytes": stat.st_size,
            })

    files.sort(key=lambda item: item["path"])
    return {
        "schema": "ai-gate.inventory.v1",
        "generated_at": utc_now(),
        "root": str(root),
        "total_files": len(files),
        "files": files,
        "excluded_dirs": sorted(DEFAULT_EXCLUDES),
    }


class EvidenceRunBuilder:
    def __init__(
        self,
        project_dir: str,
        evidence_root: str,
        task_type: str,
        agent: str,
        session_id: str,
        policy: str = "",
        run_id: str | None = None,
        run_path: str | None = None,
        late_snapshot: bool = False,
    ) -> None:
        self.project_dir = str(Path(project_dir).expanduser().resolve())
        self.evidence_root = str(Path(evidence_root).expanduser().resolve())
        self.task_type = task_type or "audit"
        self.agent = agent
        self.session_id = session_id or "unknown"
        self.policy = policy or ""
        self.run_id = run_id or make_run_id(agent, self.session_id)
        self.run_path = Path(run_path) if run_path else Path(self.evidence_root) / "runs" / self.run_id
        self.late_snapshot = late_snapshot
        self.metadata_path = self.run_path / "metadata.json"

    @classmethod
    def create(
        cls,
        project_dir: str,
        evidence_root: str,
        task_type: str,
        agent: str,
        session_id: str,
        policy: str = "",
        late_snapshot: bool = False,
    ) -> "EvidenceRunBuilder":
        builder = cls(project_dir, evidence_root, task_type, agent, session_id, policy, late_snapshot=late_snapshot)
        builder.initialize()
        return builder

    @classmethod
    def from_existing(cls, run_path: str, project_dir: str, evidence_root: str, task_type: str, policy: str = "") -> "EvidenceRunBuilder":
        metadata_path = Path(run_path) / "metadata.json"
        metadata: dict[str, Any] = {}
        if metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                metadata = {}
        return cls(
            project_dir=project_dir,
            evidence_root=evidence_root,
            task_type=task_type,
            agent=metadata.get("agent", "unknown"),
            session_id=metadata.get("session_id", "unknown"),
            policy=policy or metadata.get("policy", ""),
            run_id=metadata.get("run_id") or Path(run_path).name,
            run_path=run_path,
            late_snapshot=bool(metadata.get("late_snapshot")),
        )

    def initialize(self) -> None:
        (self.run_path / "workspace").mkdir(parents=True, exist_ok=True)
        (self.run_path / "validation").mkdir(parents=True, exist_ok=True)
        (self.run_path / "artifacts").mkdir(parents=True, exist_ok=True)
        self._write_metadata({
            "schema": "ai-gate.run.v1",
            "run_id": self.run_id,
            "agent": self.agent,
            "session_id": self.session_id,
            "project_dir": self.project_dir,
            "evidence_root": self.evidence_root,
            "task_type": self.task_type,
            "policy": self.policy,
            "created_at": utc_now(),
            "late_snapshot": self.late_snapshot,
            "status": "RUNNING",
            "finalized_at": "",
            "decision": "",
        })
        self.capture_git_status("before")
        self.write_base_documents()
        self.update_latest("RUNNING")

    def _read_metadata(self) -> dict[str, Any]:
        if not self.metadata_path.is_file():
            return {}
        try:
            return json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_metadata(self, updates: dict[str, Any]) -> dict[str, Any]:
        data = self._read_metadata()
        data.update(updates)
        data["updated_at"] = utc_now()
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        self.metadata_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return data

    def capture_git_status(self, phase: str) -> Path:
        target = self.run_path / "workspace" / f"git_status_{phase}.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_git_status(self.project_dir, self.evidence_root), encoding="utf-8")
        return target

    def write_base_documents(self) -> None:
        brief = (
            "# Task Brief\n\n"
            f"Project directory: `{self.project_dir}`\n"
            f"Task type: `{self.task_type}`\n"
            f"Agent: `{self.agent}`\n"
            f"Session id: `{self.session_id}`\n\n"
            "Scope statement: daemon-supervised activity for this registered project directory.\n"
        )
        if self.late_snapshot:
            brief += "\nBaseline note: the daemon detected the session after activity had already started.\n"
        (self.run_path / "brief.md").write_text(brief, encoding="utf-8")

        plan = (
            "# Plan\n\n"
            "1. Capture workspace baseline and tool events for the registered project.\n"
            "2. Persist required evidence artifacts using the shared evidence builder.\n"
            "3. Run conservative validation when the task type requires code-change validation.\n"
            "4. Run ai-gate acceptance and record the terminal decision.\n"
        )
        (self.run_path / "plan.md").write_text(plan, encoding="utf-8")

        self._write_risks([])
        audit_guard = (
            "Audit guard source: automated evidence supervisor daemon.\n"
            f"Agent: {self.agent}\n"
            f"Session id: {self.session_id}\n"
            f"late_snapshot: {str(self.late_snapshot).lower()}\n"
            "Status: running; final policy violations are evaluated at finalization.\n"
        )
        (self.run_path / "validation" / "audit_guard.txt").write_text(audit_guard, encoding="utf-8")

        for kind in ("test", "lint", "typecheck"):
            path = self.run_path / "validation" / f"{kind}_output.txt"
            if not path.exists():
                path.write_text(f"Validation not run yet for {kind}.\n", encoding="utf-8")
        (self.run_path / "validation_summary.md").write_text("Validation not finalized yet.\n", encoding="utf-8")
        (self.run_path / "commands.jsonl").touch()
        (self.run_path / "transcript.jsonl").touch()
        (self.run_path / "changed_files.txt").write_text("(not finalized)\n", encoding="utf-8")
        (self.run_path / "workspace" / "diff_summary.patch").write_text("(not finalized)\n", encoding="utf-8")
        self.write_artifacts([])

    def _write_risks(self, violations: list[dict[str, Any]]) -> None:
        lines = [
            "# Risks",
            "",
            "Confidence: 0.74",
            "Evidence is accepted only through deterministic gate checks and generated artifacts.",
            "Residual risk: daemon supervision can only cover events emitted by supported adapters.",
        ]
        if self.late_snapshot:
            lines.append("late_snapshot=true: the baseline may not represent a true pre-session state.")
        if violations:
            lines.append("Captured policy violations require review before acceptance.")
            for violation in violations[:20]:
                lines.append(f"- {violation.get('severity', 'unknown')}: {violation.get('message', violation.get('rule', 'policy violation'))}")
        (self.run_path / "risks.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def write_artifacts(self, violations: list[dict[str, Any]]) -> None:
        inventory = _inventory(self.project_dir, self.evidence_root)
        (self.run_path / "artifacts" / "inventory.json").write_text(
            json.dumps(inventory, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        findings = {
            "schema": "ai-gate.extracted_findings.v1",
            "generated_at": utc_now(),
            "agent": self.agent,
            "session_id": self.session_id,
            "event_count": self._event_count(),
            "violations": violations,
            "late_snapshot": self.late_snapshot,
            "changed_files": _git_changed_files(self.project_dir, self.evidence_root),
        }
        (self.run_path / "artifacts" / "extracted_findings.json").write_text(
            json.dumps(findings, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _event_count(self) -> int:
        path = self.run_path / "transcript.jsonl"
        if not path.is_file():
            return 0
        return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())

    def append_event(self, event: dict[str, Any]) -> None:
        self.run_path.mkdir(parents=True, exist_ok=True)
        event = dict(event)
        event.setdefault("timestamp", utc_now())
        event.setdefault("agent", self.agent)
        event.setdefault("session_id", self.session_id)
        event.setdefault("source", self.agent)
        event.setdefault("role", "tool")
        event.setdefault("type", "tool_result" if event.get("output") or event.get("content") else "hook_event")

        with (self.run_path / "transcript.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

        command = event.get("command") or ""
        tool_input = event.get("tool_input") or {}
        if not command and isinstance(tool_input, dict):
            command = tool_input.get("command") or ""
        if command:
            entry = {
                "timestamp": event.get("timestamp"),
                "agent": self.agent,
                "session_id": self.session_id,
                "tool_name": event.get("tool_name", ""),
                "command": command,
                "event_id": event.get("event_id") or event.get("tool_use_id") or event.get("part_rowid"),
            }
            with (self.run_path / "commands.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _collect_violations(self) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        path = self.run_path / "transcript.jsonl"
        if not path.is_file():
            return violations
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for violation in event.get("violations") or []:
                    if isinstance(violation, dict):
                        violations.append(violation)
        return violations

    def finalize(
        self,
        policy_dir: str,
        validation_timeout: int = 120,
        run_validation_step: bool = True,
    ) -> dict[str, Any]:
        self.capture_git_status("after")
        (self.run_path / "workspace" / "diff_summary.patch").write_text(_git_diff(self.project_dir), encoding="utf-8")
        changed = _git_changed_files(self.project_dir, self.evidence_root)
        (self.run_path / "changed_files.txt").write_text(
            ("\n".join(changed) + "\n") if changed else "(no file changes detected)\n",
            encoding="utf-8",
        )

        violations = self._collect_violations()
        self._write_risks(violations)
        self.write_artifacts(violations)

        if run_validation_step:
            validation = run_validation(
                project_dir=self.project_dir,
                validation_dir=str(self.run_path / "validation"),
                task_type=self.task_type,
                timeout_seconds=validation_timeout,
            )
        else:
            validation = {"status": "skipped", "results": {}}

        audit_guard_status = "passed" if not violations else "failed"
        audit_guard = (
            "Audit guard source: automated evidence supervisor daemon.\n"
            f"Agent: {self.agent}\n"
            f"Session id: {self.session_id}\n"
            f"late_snapshot: {str(self.late_snapshot).lower()}\n"
            f"Captured policy violations: {len(violations)}\n"
            f"Validation status: {validation.get('status', 'unknown')}\n"
            f"Status: {audit_guard_status}\n"
        )
        (self.run_path / "validation" / "audit_guard.txt").write_text(audit_guard, encoding="utf-8")

        closeout = (
            "# Closeout\n\n"
            f"Run id: `{self.run_id}`\n"
            f"Agent: `{self.agent}`\n"
            f"Session id: `{self.session_id}`\n"
            f"Events captured: {self._event_count()}\n"
            f"Policy violations captured: {len(violations)}\n"
            f"Validation status: {validation.get('status', 'unknown')}\n"
            "Final decision is recorded in the acceptance report generated in this run folder.\n"
        )
        (self.run_path / "closeout.md").write_text(closeout, encoding="utf-8")

        transcript_path = self.run_path / "transcript.jsonl"
        if not transcript_path.read_text(encoding="utf-8", errors="replace").strip():
            self.append_event({
                "type": "supervisor_event",
                "role": "tool",
                "tool_name": "ai-gate-daemon",
                "content": "No tool events were captured before finalization.",
                "violations": [],
            })

        report = run_acceptance(
            repo_path=self.project_dir,
            evidence_path=str(self.run_path),
            transcript_path=str(transcript_path),
            policy_dir=policy_dir,
            policy_name=self.policy or None,
            task_type=self.task_type,
        )
        md_path, json_path = write_report(report, str(self.run_path))
        csv_path = write_csv_report(report, str(self.run_path))

        metadata = self._write_metadata({
            "status": report.decision.value,
            "decision": report.decision.value,
            "finalized_at": utc_now(),
            "required_next_action": report.required_next_action,
            "acceptance_report_md": str(md_path),
            "acceptance_report_json": str(json_path),
            "acceptance_report_csv": str(csv_path),
            "validation": validation,
        })
        self.update_latest(report.decision.value, report.required_next_action)
        return metadata

    def update_latest(self, status: str, next_action: str = "") -> None:
        root = Path(self.evidence_root)
        root.mkdir(parents=True, exist_ok=True)
        latest = {
            "schema": "ai-gate.latest.v1",
            "updated_at": utc_now(),
            "run_id": self.run_id,
            "run_path": str(self.run_path),
            "project_dir": self.project_dir,
            "agent": self.agent,
            "session_id": self.session_id,
            "task_type": self.task_type,
            "status": status,
            "decision": status if status != "RUNNING" else "",
            "required_next_action": next_action,
            "late_snapshot": self.late_snapshot,
        }
        (root / "latest.json").write_text(json.dumps(latest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
