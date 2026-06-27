"""
Codex hook adapter — normalizes Codex hook stdin into ai-gate event schema,
applies PreToolUse guards, and captures evidence.

Usage as a Codex hook:
    python -m prove_it_ai_gate.codex_hooks

Codex invokes this with JSON on stdin:
{
    "hook_type": "pre_tool_use",
    "tool_name": "bash",
    "tool_input": {"command": "rm -rf /", "file_path": "src/foo.py"},
    ...
}

Returns no stdout for normal allow paths. For blocked PreToolUse calls,
returns Codex's hookSpecificOutput permission-deny JSON.

Limitations (documented):
- Codex hooks do not intercept all shell paths (e.g. inline shells in MCP tools).
- WebSearch and some non-shell/non-MCP tools are not intercepted.
- This is a best-effort guard, not complete enforcement.
- File edit guards only apply to read_only_audit task types.
"""

from __future__ import annotations

import json
import hashlib
import os
import re as _re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .daemon_state import find_project_for_cwd, get_spool_dir, load_state

# ── Bash risk patterns (shared with desktop_watcher) ──────────

BLOCKER_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+-rf\s+/(\s|$|[;&|])", "rm -rf /"),
    (r"del\s+/f\s+/s\s+[A-Za-z]:\\", "force delete drive"),
    (r"DROP\s+(TABLE|DATABASE)", "SQL DROP"),
    (r"curl.*\|\s*(ba)?sh\b", "curl | shell"),
    (r"wget\s+.*-O\s*/etc/", "wget to /etc"),
    (r">\s*/etc/", "write /etc"),
    (r"format\s+[A-Za-z]:", "format drive"),
    (r"Remove-Item\s+-LiteralPath\s+/[a-zA-Z]:", "Remove-Item root"),
    (r"git\s+push\s+--force", "git push --force"),
    (r"npm\s+unpublish\s+--force", "npm unpublish -f"),
    (r"Set-ExecutionPolicy\s+Unrestricted", "Set-ExecutionPolicy Unrestricted"),
]

WARNING_PATTERNS: list[tuple[str, str]] = [
    (r"chmod\s+777", "chmod 777"),
    (r"shutdown\s+/s", "system shutdown"),
    (r"pip\s+install.*--break-system-packages", "pip --break-system-packages"),
]

READ_ONLY_TASK_TYPES = {"audit", "review", "listing", "evaluation", "signoff"}

FILE_WRITE_TOOLS = {"write", "edit", "bash", "apply_patch"}
FILE_WRITE_COMMAND_PATTERNS = [
    _re.compile(p) for p in [
        r"^write\b", r"^edit\b", r"new-item\b", r"set-content\b",
        r"out-file\b", r">\s*\S", r">>\s*\S",
    ]
]

HOOK_EVENT_ALIASES = {
    "session_start": "SessionStart",
    "sessionstart": "SessionStart",
    "pre_tool_use": "PreToolUse",
    "pretooluse": "PreToolUse",
    "post_tool_use": "PostToolUse",
    "posttooluse": "PostToolUse",
    "stop": "Stop",
    "hook_input_error": "HookInputError",
    "hookinputerror": "HookInputError",
}


def _canonical_hook_event_name(raw: dict) -> str:
    value = str(raw.get("hook_event_name") or raw.get("hook_type") or "")
    return HOOK_EVENT_ALIASES.get(value.lower(), value)


def _canonical_tool_name(raw: dict) -> str:
    return str(raw.get("tool_name") or raw.get("tool") or "").lower()


def _stable_event_id(raw: dict, hook_event_name: str) -> str:
    payload = {
        "session_id": raw.get("session_id", ""),
        "turn_id": raw.get("turn_id", ""),
        "cwd": raw.get("cwd", ""),
        "hook_event_name": hook_event_name,
        "tool_name": raw.get("tool_name") or raw.get("tool") or "",
        "tool_use_id": raw.get("tool_use_id", ""),
        "tool_input": raw.get("tool_input", {}),
        "tool_response": (
            raw.get("tool_response")
            or raw.get("tool_output")
            or raw.get("tool_result")
            or raw.get("output")
            or raw.get("result")
            or ""
        ),
        "last_assistant_message": raw.get("last_assistant_message", ""),
        "source": raw.get("source", ""),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return "codex:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_codex_event(raw: dict) -> dict:
    """Normalize a Codex hook stdin object into the ai-gate event schema.

    Returns a dict with: type, timestamp, tool_name, command, file_path,
    hook_type, session_id, cwd, permission_mode, raw_event.
    """
    tool_input_raw = raw.get("tool_input", {}) or {}
    tool_input = tool_input_raw if isinstance(tool_input_raw, dict) else {}
    command = tool_input.get("command", "") or ""
    file_path = tool_input.get("file_path") or tool_input.get("filePath") or tool_input.get("path") or ""
    hook_event_name = _canonical_hook_event_name(raw)
    tool_name = _canonical_tool_name(raw)
    tool_response = (
        raw.get("tool_response")
        or raw.get("tool_output")
        or raw.get("tool_result")
        or raw.get("output")
        or raw.get("result")
        or ""
    )
    event_id = raw.get("hook_event_id") or raw.get("event_id") or _stable_event_id(raw, hook_event_name)

    return {
        "schema": "ai-gate.codex-hook-event.v1",
        "type": "hook_event",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "codex",
        "source": "codex_hook",
        "tool_name": tool_name,
        "command": command,
        "file_path": file_path,
        "hook_type": hook_event_name,
        "hook_event_name": hook_event_name,
        "session_id": raw.get("session_id", ""),
        "turn_id": raw.get("turn_id", ""),
        "tool_use_id": raw.get("tool_use_id", ""),
        "transcript_path": raw.get("transcript_path") or "",
        "cwd": raw.get("cwd", "") or os.getcwd(),
        "permission_mode": raw.get("permission_mode", ""),
        "model": raw.get("model", ""),
        "last_assistant_message": raw.get("last_assistant_message", ""),
        "codex_event_source": raw.get("source", ""),
        "hook_event_id": raw.get("hook_event_id", ""),
        "event_id": event_id,
        "tool_input": tool_input_raw,
        "tool_response": tool_response,
        "content": tool_response if isinstance(tool_response, str) else json.dumps(tool_response, ensure_ascii=False),
        "raw_event": raw,
    }


def _has_file_write_command(command: str) -> bool:
    if not command or not command.strip():
        return False
    cmd_stripped = command.strip()
    for pat in FILE_WRITE_COMMAND_PATTERNS:
        if pat.search(cmd_stripped.lower()):
            return True
    return False


def _guess_file_write_intent(tool_name: str, tool_input: dict) -> bool:
    """Heuristic: does this tool call intend to write to disk?"""
    if tool_name in FILE_WRITE_TOOLS:
        command = (tool_input.get("command") or "").strip()
        if command and not _has_file_write_command(command):
            # bash call without write commands — read-only shell
            return False
        return True
    return False


def check_bash_risk(command: str) -> list[dict]:
    """Check a bash command against risk patterns.

    Returns list of violations: {severity, rule, message}.
    """
    violations: list[dict] = []
    if not command or not command.strip():
        return violations

    for pattern, label in BLOCKER_PATTERNS:
        if _re.search(pattern, command):
            violations.append({
                "severity": "blocker",
                "rule": "blocker-command",
                "message": f"High-risk command ({label}): {command[:120]}",
            })
            break

    if not violations:
        for pattern, label in WARNING_PATTERNS:
            if _re.search(pattern, command):
                violations.append({
                    "severity": "warning",
                    "rule": "warning-command",
                    "message": f"Warning command ({label}): {command[:120]}",
                })
                break

    return violations


def check_file_edit(tool_name: str, command: str, file_path: str, task_type: str) -> list[dict]:
    """Guard against file edits in read-only/audit mode.

    Returns list of violations if file modification is detected during
    a read-only task.
    """
    violations: list[dict] = []

    if task_type not in READ_ONLY_TASK_TYPES:
        return violations

    # Check for explicit write/edit tools targeting repo files
    if tool_name in ("write", "edit", "apply_patch"):
        violations.append({
            "severity": "blocker",
            "rule": "read-only-file-edit",
            "message": f"File edit blocked in {task_type} mode: {tool_name} {file_path}".strip(),
        })
        return violations

    # Check for bash commands that write to files (redirect, etc.)
    if tool_name == "bash" and _has_file_write_command(command):
        violations.append({
            "severity": "warning",
            "rule": "read-only-shell-write",
            "message": f"Shell write detected in {task_type} mode: {command[:120]}",
        })

    return violations


def evaluate_pre_tool_use(normalized: dict, task_type: str) -> list[dict]:
    """Run all PreToolUse guards against a normalized event.

    Returns list of all violations found.
    """
    violations: list[dict] = []
    tool_name = normalized.get("tool_name", "")
    command = normalized.get("command", "")
    file_path = normalized.get("file_path", "")

    if tool_name == "bash" and command:
        violations.extend(check_bash_risk(command))

    violations.extend(check_file_edit(tool_name, command, file_path, task_type))

    return violations


def _is_pre_tool_use(normalized: dict) -> bool:
    value = str(normalized.get("hook_event_name") or normalized.get("hook_type") or "").lower()
    return value in {"pretooluse", "pre_tool_use"}


def _codex_stdout_response(normalized: dict, decision: str, reason: str) -> dict[str, Any] | None:
    """Build a Codex-supported hook response.

    Normal allow paths intentionally return no stdout. Unsupported allow fields
    can make Codex mark the hook run as failed and still continue the tool call.
    """
    if _is_pre_tool_use(normalized) and decision == "block":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    return None


def compute_decision(violations: list[dict]) -> tuple[str, str]:
    """Compute hook decision ('allow'|'block') and reason from violations."""
    if not violations:
        return "allow", "No policy violations detected."

    blocker_msgs = [v["message"] for v in violations if v["severity"] == "blocker"]
    warning_msgs = [v["message"] for v in violations if v["severity"] == "warning"]

    if blocker_msgs:
        return "block", "; ".join(blocker_msgs)

    if warning_msgs:
        # Warnings alone do not block, but we note them
        return "allow", "Warnings: " + "; ".join(warning_msgs)

    return "allow", "No policy violations detected."


def capture_evidence(normalized: dict, violations: list[dict], evidence_dir: str) -> str:
    """Write a normalized event to evidence/transcript.jsonl.

    Returns the path to the written transcript file.
    """
    os.makedirs(evidence_dir, exist_ok=True)
    transcript_path = os.path.join(evidence_dir, "transcript.jsonl")

    evidence_entry = {
        "timestamp": normalized["timestamp"],
        "type": "hook_event",
        "hook_type": normalized["hook_type"],
        "hook_event_name": normalized.get("hook_event_name", normalized["hook_type"]),
        "tool_name": normalized["tool_name"],
        "command": normalized.get("command", ""),
        "file_path": normalized.get("file_path", ""),
        "session_id": normalized.get("session_id", ""),
        "tool_use_id": normalized.get("tool_use_id", ""),
        "cwd": normalized.get("cwd", ""),
        "permission_mode": normalized.get("permission_mode", ""),
        "violations": [
            {"severity": v["severity"], "rule": v["rule"], "message": v["message"]}
            for v in violations
        ],
        "violation_count": len(violations),
    }

    with open(transcript_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(evidence_entry) + "\n")

    return transcript_path


def spool_event(normalized: dict, violations: list[dict], decision: str, reason: str) -> str:
    """Append a canonical Codex hook event to the daemon spool."""
    spool_dir = get_spool_dir()
    spool_dir.mkdir(parents=True, exist_ok=True)
    spool_path = spool_dir / "codex_events.jsonl"

    event = dict(normalized)
    event["violations"] = [
        {"severity": v["severity"], "rule": v["rule"], "message": v["message"]}
        for v in violations
    ]
    event["violation_count"] = len(violations)
    event["decision"] = decision
    event["reason"] = reason

    with spool_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return str(spool_path)


def _spool_fail_open(reason: str, raw: str = "") -> None:
    event = {
        "schema": "ai-gate.codex-hook-event.v1",
        "type": "hook_event",
        "agent": "codex",
        "source": "codex_hook",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hook_event_name": "HookInputError",
        "hook_type": "HookInputError",
        "cwd": os.getcwd(),
        "session_id": "",
        "turn_id": "",
        "tool_use_id": "",
        "tool_name": "",
        "event_id": f"fail-open:{datetime.now(timezone.utc).timestamp()}",
        "fail_open": True,
        "parse_failed": True,
        "content": raw[:1000],
        "violations": [{
            "severity": "blocker",
            "rule": "codex-hook-fail-open",
            "message": reason,
        }],
        "violation_count": 1,
        "decision": "allow",
        "reason": reason,
    }
    try:
        spool_dir = get_spool_dir()
        spool_dir.mkdir(parents=True, exist_ok=True)
        with (spool_dir / "codex_events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        print(f"ai-gate hook fail-open not spooled: {reason}", file=sys.stderr)


def _task_type_for_cwd(cwd: str) -> str:
    state = load_state()
    _project_key, record = find_project_for_cwd(cwd, state)
    if record:
        return record.get("task_type") or os.environ.get("AI_GATE_TASK_TYPE", "audit")
    return os.environ.get("AI_GATE_TASK_TYPE", "audit")


def run_codex_hook() -> int:
    """Main entry point: read Codex hook stdin, evaluate, capture, respond.

    Reads JSON from stdin, applies PreToolUse guards, writes evidence,
    and prints only Codex-supported hook JSON when a tool must be denied.

    Environment variables:
        AI_GATE_EVIDENCE_DIR  — where to write transcript.jsonl (default: ./evidence)
        AI_GATE_TASK_TYPE     — task type for policy resolution (default: audit)

    Returns exit code 0 (always — Codex controls allow/block via stdout JSON).
    """
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            reason = "Empty hook input; allowing."
            _spool_fail_open(reason)
            return 0

        raw_event = json.loads(raw_input)
    except json.JSONDecodeError as exc:
        _spool_fail_open(f"Hook input parse error: {exc}", raw_input if "raw_input" in locals() else "")
        return 0
    except Exception:
        _spool_fail_open("Hook input read failed.")
        return 0

    normalized = normalize_codex_event(raw_event)

    evidence_dir = os.environ.get("AI_GATE_EVIDENCE_DIR", os.path.join(os.getcwd(), "evidence"))
    task_type = _task_type_for_cwd(normalized.get("cwd", os.getcwd()))

    violations = evaluate_pre_tool_use(normalized, task_type) if _is_pre_tool_use(normalized) else []
    decision, reason = compute_decision(violations)

    try:
        spool_event(normalized, violations, decision, reason)
    except Exception as exc:
        print(f"ai-gate hook spool failure: {exc}", file=sys.stderr)

    if os.environ.get("AI_GATE_LEGACY_EVIDENCE_CAPTURE"):
        try:
            capture_evidence(normalized, violations, evidence_dir)
        except Exception:
            # Legacy capture failure should not block the hook.
            pass

    response = _codex_stdout_response(normalized, decision, reason)
    if response is not None:
        print(json.dumps(response))
    return 0


if __name__ == "__main__":
    sys.exit(run_codex_hook())
