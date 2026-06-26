"""
OpenCode Desktop Watcher — live SQLite-based guard for Windows.

Polls the OpenCode session database for new tool events, checks
them against violation rules, raises Windows toast alerts on
high-signal violations, and optionally terminates the OpenCode
process on hard violations. Persists evidence to disk and runs
final ai-gate acceptance at session end.

Usage:
    ai-gate opencode-watch [--project-dir .] [--task-type audit]
"""

from __future__ import annotations

import json
import os
import re as _re
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

_OPENCODE_DB = os.path.expanduser(r"~\.local\share\opencode\opencode.db")


# ═══════════════════════════════════════════════════════════════════
#  Violation Rules
# ═══════════════════════════════════════════════════════════════════

SECRET_PATTERNS = [
    ".env", ".secret", "credentials", "id_rsa", "id_ed25519", "id_ecdsa",
    ".pem", ".key", "private.key", "secrets.yml", "secrets.yaml",
    "secrets.json", "token", "password", "known_hosts", "authorized_keys",
    "credentials.json", "service-account.json",
]

SECRET_DIRS = [".ssh", ".aws", ".gcp", ".azure", "gcloud", ".docker"]

BLOCKER_COMMANDS = [
    (r"rm\s+-rf\s+/(\s|$|[;&|])", "rm -rf /"),
    (r"del\s+/f\s+/s\s+[A-Z]:\\", "force delete drive"),
    (r"DROP\s+(TABLE|DATABASE)", "SQL DROP"),
    (r"curl.*\|\s*(ba)?sh\b", "curl | shell"),
    (r"wget\s+.*-O\s*/etc/", "wget to /etc"),
    (r">\s*/etc/", "write /etc"),
    (r"format\s+[A-Z]:", "format drive"),
    (r"Remove-Item\s+-LiteralPath\s+/[a-zA-Z]:", "Remove-Item root"),
]

WARNING_COMMANDS = [
    (r"chmod\s+777", "chmod 777"),
    (r"git\s+push\s+--force", "git push --force"),
    (r"shutdown\s+/s", "system shutdown"),
    (r"npm\s+unpublish\s+--force", "npm unpublish -f"),
    (r"Set-ExecutionPolicy\s+Unrestricted", "exec policy"),
]


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

    Handles both event shapes:
      a) {command, file_path} at top-level (from _poll_events)
      b) {args: {command, filePath}} (from other sources)

    Returns a list of violation dicts: {severity, rule, message}
    """
    violations: list[dict] = []
    tool_name = (event.get("tool_name") or event.get("tool") or "").lower()

    # Resolve command from both shapes — top-level takes precedence,
    # fall back to args.command if top-level is empty/missing.
    top_cmd = (event.get("command") or "").strip()
    args_cmd = ((event.get("args") or {}).get("command") or "").strip()
    command = top_cmd or args_cmd

    # Resolve file_path from both shapes
    top_fp = (event.get("file_path") or "").strip()
    args_fp = ((event.get("args") or {}).get("filePath") or "").strip()
    file_path = top_fp or args_fp

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


# ═══════════════════════════════════════════════════════════════════
#  Windows Toast / Alert
# ═══════════════════════════════════════════════════════════════════

def _powershell_alert(title: str, message: str, severity: str = "info") -> None:
    """Show a Windows toast notification via PowerShell (no extra deps)."""
    escaped_title = title.replace("'", "''")
    escaped_msg = message.replace("'", "''")
    icon = "Warning" if severity in ("high", "medium") else "Info"
    ps_script = f'''
Add-Type -AssemblyName System.Windows.Forms
$balloon = New-Object System.Windows.Forms.NotifyIcon
$balloon.Icon = [System.Drawing.SystemIcons]::Warning
$balloon.BalloonTipTitle = '{escaped_title}'
$balloon.BalloonTipText = '{escaped_msg}'
$balloon.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::{icon}
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
    print(f"\n{prefix}[{datetime.now().strftime('%H:%M:%S')}] {title}: {message}")


def raise_alert(title: str, message: str, severity: str = "info") -> None:
    """Raise a user-visible alert (console + Windows toast)."""
    _console_alert(title, message, severity)
    if sys.platform == "win32":
        _powershell_alert(title, message, severity)


# ═══════════════════════════════════════════════════════════════════
#  Process Control (safe — only OpenCode-specific processes)
# ═══════════════════════════════════════════════════════════════════

_OPENCODE_PROCESS_NAMES = {"OpenCode.exe", "opencode.exe"}

# Substrings that must appear in the command line for a process
# to be considered an OpenCode sidecar (excludes generic node/bun).
_OPENCODE_CMDLINE_MARKERS = (
    "opencode-cli",
    "opencode-server",
    "@opencode-ai",
    "opencode/",
)


def _find_opencode_processes() -> list[dict]:
    """Find running OpenCode processes using WMIC for command-line inspection.

    Only returns processes whose executable name OR command line
    clearly belongs to OpenCode. Excludes generic node/bun processes.
    """
    procs: list[dict] = []
    try:
        # Phase 1: find by exact process name (fast path)
        for proc_name in _OPENCODE_PROCESS_NAMES:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {proc_name}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().splitlines():
                parts = line.replace('"', "").split(",")
                if len(parts) >= 2 and parts[1].strip().isdigit():
                    procs.append({
                        "name": parts[0].strip(),
                        "pid": int(parts[1].strip()),
                    })

        # Phase 2: find node.exe processes whose command line contains
        # OpenCode markers (sidecar running via node)
        for prow in _find_node_opencode():
            procs.append(prow)

    except Exception:
        pass
    return procs


def _find_node_opencode() -> list[dict]:
    """Find node.exe processes whose command line references OpenCode."""
    procs: list[dict] = []
    try:
        result = subprocess.run(
            ["wmic", "process", "where", "name='node.exe'",
             "get", "processid,commandline", "/format:csv"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.strip().splitlines():
            if "commandline" in line.lower():
                continue
            parts = line.split(",")
            if len(parts) < 2:
                continue
            cmdline = parts[1].strip() if len(parts) > 1 else ""
            pid_str = parts[-1].strip()
            if not pid_str.isdigit():
                continue
            if any(marker in cmdline for marker in _OPENCODE_CMDLINE_MARKERS):
                procs.append({"name": "node.exe (opencode)", "pid": int(pid_str)})
    except Exception:
        pass
    return procs


def terminate_opencode() -> bool:
    """Attempt to terminate only OpenCode-specific processes.

    Returns True if any were killed.
    """
    killed = False
    for proc in _find_opencode_processes():
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


# ═══════════════════════════════════════════════════════════════════
#  SQLite Monitor
# ═══════════════════════════════════════════════════════════════════

class OpenCodeWatcher:
    """Live SQLite-based watcher for OpenCode sessions.

    Polls the OpenCode part table for new tool events, evaluates
    violations, persists evidence to disk, and runs final gate
    acceptance at session end.
    """

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

    # ── DB helpers ─────────────────────────────────────────────

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
        """Find the latest session for this project directory."""
        candidate_dirs: list[str] = []
        p = self.project_dir.replace("\\", "/")
        for d in (p, os.path.dirname(p), os.path.dirname(os.path.dirname(p))):
            if d and d not in candidate_dirs:
                candidate_dirs.append(d)

        for cd in candidate_dirs:
            row = db.execute(
                """SELECT id FROM session
                   WHERE directory = ?
                   ORDER BY time_created DESC LIMIT 1""",
                (cd,),
            ).fetchone()
            if row:
                return row["id"]
        return None

    def _get_session_info(self, db: sqlite3.Connection) -> Optional[dict]:
        """Return {id, title} for the active session."""
        if not self._session_id:
            return None
        row = db.execute(
            "SELECT id, title FROM session WHERE id = ?",
            (self._session_id,),
        ).fetchone()
        return dict(row) if row else None

    def _poll_events(self, db: sqlite3.Connection) -> list[dict]:
        """Poll for new tool events since last check timestamp."""
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
                    "command": inp.get("command", "") or "",
                    "file_path": inp.get("filePath", "") or "",
                    "output": state.get("output", "") or "",
                    "status": state.get("status", "") or "",
                    "timestamp": row["time_created"],
                })
            except (json.JSONDecodeError, TypeError, KeyError):
                continue

        if events:
            self._last_event_ts = events[-1]["timestamp"]

        return events

    # ── Evidence persistence ───────────────────────────────────

    def _ensure_dirs(self) -> None:
        os.makedirs(self._evidence_dir, exist_ok=True)
        os.makedirs(os.path.join(self._evidence_dir, "workspace"), exist_ok=True)

    def _write_transcript_event(self, event: dict) -> None:
        """Append a single tool event to evidence/transcript.jsonl."""
        self._ensure_dirs()
        transcript_path = os.path.join(self._evidence_dir, "transcript.jsonl")
        ev = {
            "type": "tool_result",
            "role": "tool",
            "tool_name": event.get("tool_name", ""),
            "command": event.get("command", ""),
            "file_path": event.get("file_path", ""),
            "timestamp": event.get("timestamp", ""),
            "status": event.get("status", ""),
        }
        # Include output snippet (truncated)
        output = event.get("output", "")
        if output:
            ev["content"] = output[:8000]
            ev["truncated"] = len(output) > 8000
        with open(transcript_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev) + "\n")

    def _write_live_report(self) -> None:
        """Write live_watch_report.jsonl with all events + violations."""
        self._ensure_dirs()
        report_path = os.path.join(self._evidence_dir, "live_watch_report.jsonl")
        entry = {
            "timestamp": datetime.now().isoformat(),
            "events_captured": self._event_count,
            "violations_detected": self._violation_count,
            "session_id": self._session_id or "unknown",
        }
        with open(report_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def _capture_git_status(self) -> None:
        """Capture current git status to evidence/workspace/."""
        self._ensure_dirs()
        git_path = os.path.join(self._evidence_dir, "workspace", "git_status_after.txt")
        try:
            result = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True, text=True,
                cwd=self.project_dir, timeout=10,
            )
            status = result.stdout.strip() or "(clean)"
            Path(git_path).write_text(status, encoding="utf-8")
        except Exception:
            Path(git_path).write_text("(unavailable)", encoding="utf-8")

    def _write_closeout(self, db: sqlite3.Connection) -> None:
        """Write closeout.md from session metadata."""
        self._ensure_dirs()
        session = self._get_session_info(db)
        title = session["title"] if session else "OpenCode Session"
        sid = session["id"][:20] if session else "unknown"
        closeout = (
            f"# {title}\n\n"
            f"Session ID: {sid}\n"
            f"Finished: {datetime.now().isoformat()}\n\n"
            f"Confidence: 0.85 — live-watched by prove-it-ai-gate ({self.task_type}).\n"
            f"Events captured: {self._event_count}\n"
            f"Violations detected: {self._violation_count}\n"
        )
        co_path = os.path.join(self._evidence_dir, "closeout.md")
        Path(co_path).write_text(closeout, encoding="utf-8")

    def _evidence_exists(self) -> bool:
        """Check if transcript.jsonl has any content."""
        tx_path = os.path.join(self._evidence_dir, "transcript.jsonl")
        return os.path.isfile(tx_path) and os.path.getsize(tx_path) > 0

    # ── Gate ───────────────────────────────────────────────────

    def _run_gate(self) -> Optional[int]:
        """Run final ai-gate acceptance. Returns exit code or None if skipped."""
        if not self._evidence_exists():
            print("  (skipping gate: no transcript evidence)")
            return None

        transcript_path = os.path.join(self._evidence_dir, "transcript.jsonl")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "prove_it_ai_gate.cli", "accept",
                 "--repo", self.project_dir,
                 "--evidence", self._evidence_dir,
                 "--transcript", transcript_path,
                 "--task-type", self.task_type],
                cwd=self.project_dir,
                capture_output=False,
                timeout=60,
            )
            return result.returncode
        except subprocess.TimeoutExpired:
            print("  Gate timed out")
            return None
        except Exception as exc:
            print(f"  Gate error: {exc}")
            return None

    def _print_gate_summary(self, exit_code: Optional[int]) -> None:
        decisions = {0: "ACCEPT", 1: "REJECT", 2: "ACCEPT_WITH_CONDITIONS", 3: "BLOCKED"}
        decision = decisions.get(exit_code, "UNKNOWN") if exit_code is not None else "SKIPPED"
        print(f"Gate decision: {decision}" + (f" (exit {exit_code})" if exit_code is not None else ""))

    # ── Main loop ─────────────────────────────────────────────

    def start(self) -> None:
        """Main watch loop. Blocks until interrupted or session ends."""
        print(f"=== prove-it-ai-gate Desktop Watcher ===")
        print(f"Project:     {self.project_dir}")
        print(f"Task type:   {self.task_type}")
        print(f"Poll:        {self.poll_interval}s")
        print(f"Auto-kill:   {self.auto_terminate}")
        print(f"Evidence:    {self._evidence_dir}")
        print(f"Watching DB: {_OPENCODE_DB}")
        print()
        print("Waiting for OpenCode session... (Ctrl+C to stop)")
        print()

        self._running = True
        session_active = False
        final_gate_ran = False

        def _shutdown() -> None:
            """Clean shutdown: capture final state, run gate."""
            nonlocal final_gate_ran
            if final_gate_ran:
                return
            final_gate_ran = True
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Shutting down...")
            # Reconnect to get latest session info for closeout
            db = self._connect_db()
            if db:
                try:
                    self._write_closeout(db)
                except Exception:
                    pass
                db.close()
            self._capture_git_status()
            self._write_live_report()
            print(f"Events captured: {self._event_count}")
            print(f"Violations detected: {self._violation_count}")
            if self._evidence_exists():
                print("Running final gate...")
                ec = self._run_gate()
                self._print_gate_summary(ec)
            else:
                print("No transcript evidence — skipping gate.")

        # Register signal handler for Ctrl+C graceful shutdown
        try:
            original_sigint = signal.getsignal(signal.SIGINT)

            def _sigint_handler(signum, frame):
                _shutdown()
                # Restore original handler and re-raise
                signal.signal(signal.SIGINT, original_sigint)
                raise KeyboardInterrupt

            signal.signal(signal.SIGINT, _sigint_handler)
        except (ValueError, OSError):
            pass  # signal handler not available (e.g., in some threads)

        try:
            while self._running:
                try:
                    db = self._connect_db()
                    if not db:
                        if session_active:
                            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] OpenCode DB disappeared.")
                            _shutdown()
                            break
                        time.sleep(self.poll_interval)
                        continue

                    # Find / refresh session
                    sid = self._find_session(db)
                    if sid and not session_active:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Session detected: {sid[:20]}...")
                        session_active = True
                        self._session_id = sid
                        self._capture_git_status()
                        self._write_live_report()
                    elif not sid and session_active:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Session ended (no active session found).")
                        _shutdown()
                        break
                    elif sid and sid != self._session_id:
                        # New session started — run gate on previous, reset
                        if self._event_count > 0:
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] New session detected. Finalizing previous...")
                            _shutdown()
                        self._session_id = sid
                        self._event_count = 0
                        self._violation_count = 0
                        self._last_event_ts = None

                    # Poll for events
                    events = self._poll_events(db)
                    for ev in events:
                        self._event_count += 1
                        self._write_transcript_event(ev)

                        violations = evaluate_event(ev)
                        tool = ev.get("tool_name") or ev.get("tool") or "?"
                        detail = ev.get("command") or ev.get("file_path") or ""
                        ts = ev.get("timestamp", "")[:19] if ev.get("timestamp") else ""
                        print(f"  [{ts}] {tool}: {detail[:80]}")

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

                    # Write live report on each cycle
                    if events:
                        self._write_live_report()

                    db.close()

                except KeyboardInterrupt:
                    break
                except Exception as exc:
                    print(f"  Watcher error: {exc}")
                    time.sleep(self.poll_interval)
                    continue

                time.sleep(self.poll_interval)

        finally:
            _shutdown()


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
