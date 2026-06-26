"""
OpenCode Desktop Watcher — live SQLite-based guard for Windows.

Polls the OpenCode session database for new tool events, checks
them against violation rules, raises Windows toast alerts on
high-signal violations, and optionally terminates the OpenCode
process on hard violations. Runs final ai-gate acceptance at
session end.

Usage:
    ai-gate opencode-watch [--project-dir .] [--task-type audit]
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

_OPENCODE_DB = os.path.expanduser(r"~\.local\share\opencode\opencode.db")


# ── Violation Rules ──────────────────────────────────────────────

SECRET_PATTERNS = [
    ".env", ".secret", "credentials", "id_rsa", "id_ed25519", "id_ecdsa",
    ".pem", ".key", "private.key", "secrets.yml", "secrets.yaml",
    "secrets.json", "token", "password", "known_hosts", "authorized_keys",
    "credentials.json", "service-account.json",
]

SECRET_DIRS = [".ssh", ".aws", ".gcp", ".azure", "gcloud", ".docker"]

BLOCKER_COMMANDS = [
    (r"rm\s+-rf\s+/(\s|$|[;&|])", "rm -rf /"),
    (r"DROP\s+(TABLE|DATABASE)", "SQL DROP"),
    (r"curl.*\|\s*(ba)?sh\b", "curl | shell"),
    (r">\s*/etc/", "write /etc"),
    (r"format\s+[A-Z]:", "format drive"),
]

WARNING_COMMANDS = [
    (r"chmod\s+777", "chmod 777"),
    (r"git\s+push\s+--force", "git push --force"),
    (r"shutdown\s+/s", "system shutdown"),
    (r"npm\s+unpublish\s+--force", "npm unpublish -f"),
    (r"Set-ExecutionPolicy\s+Unrestricted", "exec policy"),
]

import re as _re


def _is_secret_file(file_path: str) -> bool:
    if not file_path:
        return False
    low = file_path.lower().replace("\\", "/")
    name = low.rsplit("/", 1)[-1]
    for p in SECRET_PATTERNS:
        if name == p or name.startswith(p) or name.endswith(p):
            return True
    for d in SECRET_DIRS:
        if low == d or low.startswith(d + "/") or ("/" + d + "/") in low:
            return True
    return False


def evaluate_event(event: dict) -> list[dict]:
    """Check a tool event against all violation rules.

    Returns a list of violation dicts: {severity, rule, message}
    """
    violations: list[dict] = []
    tool_name = (event.get("tool_name") or event.get("tool") or "").lower()
    args = event.get("args") or event.get("input") or {}
    command = (args.get("command") or "").strip()
    file_path = (args.get("filePath") or args.get("file_path") or "").strip()

    # Rule 1: Secret / sensitive file reads
    if tool_name in ("read", "glob") and _is_secret_file(file_path):
        violations.append({
            "severity": "high",
            "rule": "secret-file-access",
            "message": f"Sensitive file accessed: {file_path}",
        })

    # Rule 2: Blocker-tier bash commands
    if tool_name == "bash" and command:
        for pattern, label in BLOCKER_COMMANDS:
            if _re.search(pattern, command):
                violations.append({
                    "severity": "high",
                    "rule": "blocker-command",
                    "message": f"High-risk command ({label}): {command[:120]}",
                })
                break

        # Rule 3: Warning-tier bash commands
        for pattern, label in WARNING_COMMANDS:
            if _re.search(pattern, command):
                violations.append({
                    "severity": "medium",
                    "rule": "warning-command",
                    "message": f"Risky command ({label}): {command[:120]}",
                })
                break

    return violations


# ── Windows Toast / Alert ─────────────────────────────────────────

def _powershell_alert(title: str, message: str, severity: str = "info") -> None:
    """Show a Windows toast notification via PowerShell (no extra deps)."""
    escaped_title = title.replace("'", "''")
    escaped_msg = message.replace("'", "''")
    ps_script = f'''
Add-Type -AssemblyName System.Windows.Forms
$balloon = New-Object System.Windows.Forms.NotifyIcon
$balloon.Icon = [System.Drawing.SystemIcons]::Warning
$balloon.BalloonTipTitle = '{escaped_title}'
$balloon.BalloonTipText = '{escaped_msg}'
$balloon.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::{
    "Warning" if severity in ("high", "medium") else "Info"
}
$balloon.Visible = $true
$balloon.ShowBalloonTip(5000)
Start-Sleep -Seconds 6
$balloon.Dispose()
'''
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", ps_script],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception:
        pass


def _console_alert(title: str, message: str, severity: str = "info") -> None:
    """Fallback: print alert to stdout."""
    prefix = "*** " if severity == "high" else "!! " if severity == "medium" else "   "
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n{prefix}[{ts}] {title}: {message}")


def raise_alert(title: str, message: str, severity: str = "info") -> None:
    """Raise a user-visible alert."""
    _console_alert(title, message, severity)
    if sys.platform == "win32":
        _powershell_alert(title, message, severity)


# ── Process Control ───────────────────────────────────────────────

def find_opencode_processes() -> list[dict]:
    """Find running OpenCode-related processes."""
    procs: list[dict] = []
    try:
        # Look for opencode sidecar / desktop
        for proc_name in ("opencode", "node", "bun", "OpenCode.exe"):
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {proc_name}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().splitlines():
                if not line.strip():
                    continue
                parts = line.replace('"', "").split(",")
                if len(parts) >= 2 and parts[1].strip().isdigit():
                    procs.append({"name": parts[0].strip(), "pid": int(parts[1].strip())})
    except Exception:
        pass
    return procs


def terminate_opencode() -> bool:
    """Attempt to terminate OpenCode processes. Returns True if any were killed."""
    killed = False
    for proc in find_opencode_processes():
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc["pid"]), "/F"],
                capture_output=True, timeout=5,
            )
            print(f"  Terminated: {proc['name']} (PID {proc['pid']})")
            killed = True
        except Exception:
            pass
    return killed


# ── SQLite Monitor ────────────────────────────────────────────────

class OpenCodeWatcher:
    """Live SQLite-based watcher for OpenCode sessions."""

    def __init__(
        self,
        project_dir: str,
        task_type: str = "audit",
        poll_interval: float = 2.0,
        on_violation: Optional[Callable] = None,
        auto_terminate: bool = False,
    ):
        self.project_dir = os.path.abspath(project_dir)
        self.task_type = task_type
        self.poll_interval = poll_interval
        self.on_violation = on_violation
        self.auto_terminate = auto_terminate
        self._last_event_ts: Optional[str] = None
        self._session_id: Optional[str] = None
        self._evidence_dir = os.path.join(self.project_dir, "evidence")
        self._event_count = 0
        self._violation_count = 0
        self._running = False

    def _connect_db(self) -> Optional[sqlite3.Connection]:
        if not os.path.isfile(_OPENCODE_DB):
            return None
        try:
            db = sqlite3.connect(_OPENCODE_DB)
            db.row_factory = sqlite3.Row
            return db
        except Exception:
            return None

    def _find_session(self, db: sqlite3.Connection) -> Optional[str]:
        """Find the active session for this project directory."""
        candidate_dirs = []
        p = self.project_dir.replace("\\", "/")
        for d in (p, os.path.dirname(p), os.path.dirname(os.path.dirname(p))):
            if d and d not in candidate_dirs:
                candidate_dirs.append(d)

        for cd in candidate_dirs:
            row = db.execute(
                "SELECT id FROM session WHERE directory = ? ORDER BY time_created DESC LIMIT 1",
                (cd,),
            ).fetchone()
            if row:
                return row["id"]
        return None

    def _poll_events(self, db: sqlite3.Connection) -> list[dict]:
        """Poll for new tool events since last check."""
        if not self._session_id:
            return []

        if self._last_event_ts:
            rows = db.execute(
                """SELECT rowid, time_created, data FROM part
                   WHERE session_id = ? AND time_created > ?
                   ORDER BY time_created""",
                (self._session_id, self._last_event_ts),
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT rowid, time_created, data FROM part
                   WHERE session_id = ?
                   ORDER BY time_created""",
                (self._session_id,),
            ).fetchall()

        events: list[dict] = []
        for row in rows:
            try:
                data = json.loads(row["data"])
                if data.get("type") != "tool":
                    continue
                state = data.get("state", {})
                inp = state.get("input", {})
                events.append({
                    "tool": data.get("tool", ""),
                    "tool_name": data.get("tool", ""),
                    "command": inp.get("command", ""),
                    "file_path": inp.get("filePath", ""),
                    "output": state.get("output", ""),
                    "status": state.get("status", ""),
                    "timestamp": row["time_created"],
                })
            except (json.JSONDecodeError, TypeError, KeyError):
                continue

        if events:
            self._last_event_ts = events[-1]["timestamp"]

        return events

    def _run_gate(self) -> int:
        """Run the final acceptance gate on current evidence."""
        os.makedirs(self._evidence_dir, exist_ok=True)
        transcript_path = os.path.join(self._evidence_dir, "transcript.jsonl")
        git_status = os.path.join(self._evidence_dir, "workspace", "git_status_after.txt")

        return subprocess.run(
            [sys.executable, "-m", "prove_it_ai_gate.cli", "accept",
             "--repo", self.project_dir,
             "--evidence", self._evidence_dir,
             "--transcript", transcript_path,
             "--task-type", self.task_type],
            cwd=self.project_dir,
            capture_output=False,
        ).returncode

    def start(self) -> None:
        """Main watch loop. Blocks until interrupted."""
        print(f"=== prove-it-ai-gate Desktop Watcher ===")
        print(f"Project: {self.project_dir}")
        print(f"Task type: {self.task_type}")
        print(f"Poll interval: {self.poll_interval}s")
        print(f"Auto-terminate: {self.auto_terminate}")
        print(f"Watching OpenCode DB: {_OPENCODE_DB}")
        print()
        print("Waiting for OpenCode session...")
        print("(Press Ctrl+C to stop)")
        print()

        self._running = True
        session_active = False

        while self._running:
            try:
                db = self._connect_db()
                if not db:
                    if session_active:
                        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Session ended (DB gone).")
                        self._run_final_gate()
                        break
                    time.sleep(self.poll_interval)
                    continue

                # Find / refresh session
                sid = self._find_session(db)
                if sid and not session_active:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Session detected: {sid[:20]}...")
                    session_active = True
                    self._session_id = sid
                elif not sid and session_active:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Session ended.")
                    self._run_final_gate()
                    break
                elif sid and sid != self._session_id:
                    self._session_id = sid

                # Poll for events
                events = self._poll_events(db)
                for ev in events:
                    self._event_count += 1
                    violations = evaluate_event(ev)
                    tool = ev.get("tool_name") or ev.get("tool") or "?"
                    print(f"  [{ev.get('timestamp', '')}] {tool}: {ev.get('command', ev.get('file_path', ''))[:80]}")

                    for v in violations:
                        self._violation_count += 1
                        sev = v["severity"]
                        msg = v["message"]
                        print(f"    VIOLATION [{sev.upper()}] {msg}")
                        raise_alert(
                            f"prove-it-ai-gate: {sev.upper()} Violation",
                            msg,
                            severity=sev,
                        )
                        if sev == "high" and self.auto_terminate:
                            print(f"    Auto-terminating OpenCode...")
                            terminate_opencode()

                db.close()

            except KeyboardInterrupt:
                print("\nWatcher stopped by user.")
                break
            except Exception as exc:
                print(f"  Watcher error: {exc}")
                time.sleep(self.poll_interval)
                continue

            time.sleep(self.poll_interval)

    def _run_final_gate(self) -> None:
        """Run acceptance and print summary."""
        print(f"\nEvents captured: {self._event_count}")
        print(f"Violations detected: {self._violation_count}")
        print(f"Running final gate...")
        exit_code = self._run_gate()
        decisions = {0: "ACCEPT", 1: "REJECT", 2: "ACCEPT_WITH_CONDITIONS", 3: "BLOCKED"}
        decision = decisions.get(exit_code, "UNKNOWN")
        print(f"Gate decision: {decision} (exit {exit_code})")


def start_watcher(
    project_dir: str = ".",
    task_type: str = "audit",
    poll_interval: float = 2.0,
    auto_terminate: bool = False,
) -> None:
    """Entry point for the watcher."""
    watcher = OpenCodeWatcher(
        project_dir=os.path.abspath(project_dir),
        task_type=task_type,
        poll_interval=poll_interval,
        auto_terminate=auto_terminate,
    )
    watcher.start()
