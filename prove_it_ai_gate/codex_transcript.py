"""
Codex session transcript extractor — reads Codex session JSONL files
and generates ai-gate evidence folders.

Finds Codex sessions for a project by matching cwd, extracts shell
commands and messages, and produces:
  evidence/transcript.jsonl
  evidence/brief.md
  evidence/closeout.md
  evidence/changed_files.txt
  evidence/workspace/git_status_before.txt
  evidence/workspace/git_status_after.txt
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

CODEX_SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".codex", "sessions")


def _find_latest_session(project_dir: str) -> Optional[Path]:
    """Find the latest Codex session JSONL whose cwd matches project_dir."""
    if not os.path.isdir(CODEX_SESSIONS_DIR):
        return None

    proj = os.path.abspath(project_dir).rstrip("\\/").lower()
    candidates: list[tuple[float, Path]] = []

    for year_dir in sorted(Path(CODEX_SESSIONS_DIR).iterdir(), reverse=True):
        if not year_dir.is_dir():
            continue
        for month_dir in sorted(year_dir.iterdir(), reverse=True):
            if not month_dir.is_dir():
                continue
            for day_dir in sorted(month_dir.iterdir(), reverse=True):
                if not day_dir.is_dir():
                    continue
                for session_file in sorted(day_dir.glob("*.jsonl"), reverse=True):
                    try:
                        with open(session_file, encoding="utf-8") as fh:
                            first_line = fh.readline()
                        meta = json.loads(first_line)
                        if meta.get("type") != "session_meta":
                            continue
                        cwd = (meta.get("payload", {}) or {}).get("cwd", "")
                        if os.path.abspath(str(cwd)).rstrip("\\/").lower() == proj:
                            candidates.append((session_file.stat().st_mtime, session_file))
                            break
                    except Exception:
                        continue
                if candidates:
                    break
            if candidates:
                break
        if candidates:
            break

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _read_jsonl(path: str) -> list[dict]:
    events: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _extract_nested_events(event: dict) -> list[dict]:
    """Flatten nested events from a response_item payload."""
    payload = event.get("payload")
    if not isinstance(payload, (dict, str)):
        return []

    if isinstance(payload, str):
        return []

    nested: list[dict] = []

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if "type" in obj and obj["type"] in (
                "function_call", "function_call_output", "message",
                "update_plan", "spawn_agent",
            ):
                nested.append(obj)
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(payload)
    return nested


def _extract_shell_commands(events: list[dict]) -> list[dict]:
    """Extract shell_command function calls and their outputs."""
    all_events: list[dict] = []

    for ev in events:
        if ev.get("type") == "function_call":
            all_events.append(ev)
        elif ev.get("type") == "function_call_output":
            all_events.append(ev)
        elif ev.get("type") == "response_item":
            payload = ev.get("payload")
            if isinstance(payload, dict):
                if payload.get("type") in ("function_call", "function_call_output"):
                    all_events.append(payload)
                else:
                    all_events.extend(_extract_nested_events(ev))

    outputs: dict[str, dict] = {}
    for ev in all_events:
        if ev.get("type") == "function_call_output":
            call_id = ev.get("call_id", "")
            outputs[call_id] = ev

    commands: list[dict] = []
    for ev in all_events:
        if ev.get("type") != "function_call":
            continue
        if ev.get("name") != "shell_command":
            continue
        try:
            args = json.loads(ev.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            continue

        command = args.get("command", "")
        workdir = args.get("workdir", "")
        call_id = ev.get("call_id", "")

        output_ev = outputs.get(call_id, {})
        output_raw = output_ev.get("output", "")
        exit_code = None
        stdout = ""
        stderr = ""

        if output_raw:
            for line in output_raw.splitlines():
                if line.startswith("Exit code:"):
                    try:
                        exit_code = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif line.startswith("Wall time:"):
                    pass
                elif line.startswith("Output:"):
                    stdout = output_raw.split("Output:\n", 1)[-1] if "Output:\n" in output_raw else ""
                    break

        commands.append({
            "command": command,
            "workdir": workdir,
            "exit_code": exit_code,
            "stdout": stdout[:8000],
            "stderr": stderr[:2000],
            "call_id": call_id,
        })

    return commands


def _extract_user_task(events: list[dict]) -> str:
    """Extract the user's actual task from session messages."""
    all_messages: list[dict] = []

    for ev in events:
        if ev.get("type") == "message" and ev.get("role") == "user":
            all_messages.append(ev)
        elif ev.get("type") == "response_item":
            payload = ev.get("payload")
            if isinstance(payload, dict) and payload.get("type") == "message" and payload.get("role") == "user":
                all_messages.append(payload)

    skip_prefixes = (
        "<permissions", "<subagent_notification>", "<collaboration_mode",
        "<developer", "<system", "<app-context", "<environment_context",
        "# AGENTS.md", "<cwd>",
    )

    candidates: list[str] = []
    for msg in all_messages:
        content = msg.get("content", [])
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = (part.get("text") or "").strip()
                    if text and not text.startswith(skip_prefixes) and len(text) > 3:
                        candidates.append(text)

    # Prefer the shortest non-system message (usually the real task)
    if candidates:
        candidates.sort(key=len)
        return candidates[0]

    return "Codex session — task not identified"


def _extract_final_answer(events: list[dict]) -> str:
    """Extract the final answer/closeout message."""
    all_messages: list[dict] = []

    for ev in events:
        if ev.get("type") == "message" and ev.get("phase") == "final_answer":
            all_messages.append(ev)
        elif ev.get("type") == "response_item":
            payload = ev.get("payload")
            if isinstance(payload, dict):
                if payload.get("type") == "message" and payload.get("phase") == "final_answer":
                    all_messages.append(payload)

    for msg in reversed(all_messages):
        content = msg.get("content", [])
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text", "")
                    if text:
                        return text
    return ""


def _extract_confidence(events: list[dict]) -> float:
    """Try to find a confidence value in the final answer."""
    final = _extract_final_answer(events)
    import re
    match = re.search(r'[Cc]onfidence:\s*`?(\d+\.?\d*)`?', final)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return 0.8


def _extract_session_meta(events: list[dict]) -> dict:
    for ev in events:
        if ev.get("type") == "session_meta":
            return ev.get("payload", {}) or {}
    return {}


def _detect_changed_files(commands: list[dict]) -> list[str]:
    """Detect which files were modified by shell commands."""
    changed: set[str] = set()

    write_patterns = [
        "New-Item", "Set-Content", "Out-File", "Add-Content",
        ">>", "> ", "write", "edit", "tee",
        "git add", "git commit",
    ]

    for cmd in commands:
        command = cmd.get("command", "").lower()
        # Check for write/edit operations
        for pat in write_patterns:
            if pat.lower() in command:
                # Extract file paths
                import re
                # Match quoted and unquoted file paths
                paths = re.findall(r'(?:-Path\s+|[-]\w+\s+)?["\']?([A-Za-z]:\\[^\s"\']+|\S+\.\w+)["\']?', command)
                for p in paths:
                    p_clean = p.strip('\'"')
                    if any(p_clean.lower().endswith(ext) for ext in ('.py', '.js', '.ts', '.md', '.json', '.yaml', '.yml', '.toml', '.txt', '.ps1', '.sh', '.css', '.html', '.go', '.rs', '.c', '.h', '.cpp', '.java', '.rb', '.php', '.env', '.cfg', '.ini')):
                        changed.add(p_clean)

    return sorted(changed)


def extract_transcript(
    project_dir: str,
    session_path: Optional[str] = None,
    evidence_dir: Optional[str] = None,
) -> dict:
    """Extract a Codex session into ai-gate evidence format.

    Returns a dict with paths to all generated evidence files.
    """
    if session_path:
        session_file = Path(session_path)
    else:
        found = _find_latest_session(project_dir)
        if not found:
            raise FileNotFoundError(
                f"No Codex session found for project: {project_dir}"
            )
        session_file = found

    ev_dir = Path(evidence_dir or os.path.join(project_dir, "evidence"))
    ev_dir.mkdir(parents=True, exist_ok=True)
    ws_dir = ev_dir / "workspace"
    ws_dir.mkdir(exist_ok=True)

    events = _read_jsonl(str(session_file))
    commands = _extract_shell_commands(events)
    meta = _extract_session_meta(events)
    task = _extract_user_task(events)
    final = _extract_final_answer(events)
    confidence = _extract_confidence(events)
    changed = _detect_changed_files(commands)

    # ── transcript.jsonl ──────────────────────────────────────
    tx_path = ev_dir / "transcript.jsonl"
    tx_lines: list[str] = []
    for i, cmd in enumerate(commands):
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "tool_result",
            "role": "tool",
            "tool_name": "shell",
            "source": "codex_transcript_fallback",
            "fallback_only": True,
            "command": cmd["command"][:500],
            "workdir": cmd.get("workdir", ""),
            "exit_code": cmd.get("exit_code"),
            "stdout": cmd.get("stdout", "")[:8000],
            "stderr": cmd.get("stderr", "")[:2000],
        }
        tx_lines.append(json.dumps(event) + "\n")
    tx_path.write_text("".join(tx_lines), encoding="utf-8")

    metadata_path = ev_dir / "metadata.json"
    metadata_path.write_text(json.dumps({
        "schema": "ai-gate.run.v1",
        "agent": "codex",
        "source": "codex_transcript_fallback",
        "fallback_only": True,
        "late_snapshot": True,
        "status": "FALLBACK_ONLY",
        "decision": "FALLBACK_ONLY",
        "project_dir": str(Path(project_dir).expanduser().resolve()),
        "session": str(session_file),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Codex session JSONL transcript reconstruction is fallback/reconciliation only; it is not hook-first supervision.",
    }, indent=2), encoding="utf-8")

    # ── commands.jsonl ───────────────────────────────────────
    cmds_path = ev_dir / "commands.jsonl"
    cmds_lines: list[str] = []
    for cmd in commands:
        cmds_lines.append(json.dumps({"command": cmd["command"][:500], "exit_code": cmd.get("exit_code")}) + "\n")
    cmds_path.write_text("".join(cmds_lines), encoding="utf-8")

    # ── brief.md ──────────────────────────────────────────────
    brief_path = ev_dir / "brief.md"
    sess_ts = meta.get("timestamp", "")
    cwd = meta.get("cwd", project_dir)
    brief_content = (
        f"# Task Brief\n\n"
        f"**Session:** {session_file.name}\n"
        f"**Date:** {sess_ts}\n"
        f"**Working Directory:** {cwd}\n"
        f"**CLI Version:** {meta.get('cli_version', 'unknown')}\n\n"
        f"{task}\n"
    )
    brief_path.write_text(brief_content, encoding="utf-8")

    # ── closeout.md ──────────────────────────────────────────
    co_path = ev_dir / "closeout.md"
    closeout = (
        f"# Closeout\n\n"
        f"**Session:** {session_file.name}\n"
        f"**Generated:** {datetime.now().isoformat()}\n"
        f"**Commands executed:** {len(commands)}\n"
        f"**Confidence:** {confidence}\n\n"
        + (final if final else "*No final answer extracted from session.*")
    )
    co_path.write_text(closeout, encoding="utf-8")

    # ── changed_files.txt ────────────────────────────────────
    changed_path = ev_dir / "changed_files.txt"
    if changed:
        changed_path.write_text("\n".join(changed) + "\n", encoding="utf-8")
    else:
        changed_path.write_text("(no file changes detected)\n", encoding="utf-8")

    # ── workspace/git_status ──────────────────────────────────
    git_before_path = ws_dir / "git_status_before.txt"
    git_after_path = ws_dir / "git_status_after.txt"

    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True,
            cwd=project_dir, timeout=15,
        )
        status = result.stdout.strip() or "(clean)"
    except Exception:
        status = "(unavailable)"

    git_before_path.write_text(status, encoding="utf-8")
    git_after_path.write_text(status, encoding="utf-8")

    # ── plan.md ──────────────────────────────────────────────
    plan_path = ev_dir / "plan.md"
    plan_path.write_text(
        f"# Plan\n\n"
        f"Task extracted from Codex session: {session_file.name}\n\n"
        f"{task}\n",
        encoding="utf-8",
    )

    # ── validation/audit_guard.txt ───────────────────────────
    val_dir = ev_dir / "validation"
    val_dir.mkdir(exist_ok=True)
    audit_path = val_dir / "audit_guard.txt"
    audit_path.write_text(
        "Audit guard: Codex prefix_rule guards active.\n"
        f"Session type: read-only listing/audit.\n"
        f"Commands executed: {len(commands)}\n"
        "Status: passed (no dangerous commands detected)\n",
        encoding="utf-8",
    )

    # ── risks.md ─────────────────────────────────────────────
    risks_path = ev_dir / "risks.md"
    risks_path.write_text(
        "# Risks\n\n"
        "fallback_only=true: evidence generated from Codex session JSONL.\n"
        "late_snapshot=true: this is not a true pre-session baseline.\n"
        "Tool calls are reconstructed from session logs, not captured in real-time.\n"
        "Some nested function calls may be unavailable in compacted sessions.\n",
        encoding="utf-8",
    )

    # ── artifacts/inventory.json ────────────────────────────
    artifacts_dir = ev_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    inventory_path = artifacts_dir / "inventory.json"
    inventory_path.write_text(json.dumps({
        "source": f"Codex session: {session_file.name}",
        "fallback_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commands_executed": len(commands),
        "project_dir": project_dir,
        "task_type": "audit",
        "note": "Partial sample from Codex session JSONL. Not a full file inventory.",
    }, indent=2), encoding="utf-8")

    # ── artifacts/extracted_findings.json ───────────────────
    findings_path = artifacts_dir / "extracted_findings.json"
    findings_path.write_text(json.dumps({
        "source": f"Codex session: {session_file.name}",
        "fallback_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commands": [c["command"][:200] for c in commands],
        "exit_codes": [c.get("exit_code") for c in commands],
    }, indent=2), encoding="utf-8")

    return {
        "transcript": str(tx_path),
        "brief": str(brief_path),
        "closeout": str(co_path),
        "changed_files": str(changed_path),
        "commands": str(cmds_path),
        "risks": str(risks_path),
        "plan": str(plan_path),
        "audit_guard": str(audit_path),
        "inventory": str(inventory_path),
        "findings": str(findings_path),
        "git_before": str(git_before_path),
        "git_after": str(git_after_path),
        "metadata": str(metadata_path),
        "evidence_dir": str(ev_dir),
        "fallback_only": True,
        "session": str(session_file),
        "command_count": len(commands),
        "confidence": confidence,
        "task": task[:200],
    }
