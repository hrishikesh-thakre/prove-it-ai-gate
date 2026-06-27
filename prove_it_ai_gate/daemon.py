from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .daemon_state import (
    find_project_for_cwd,
    get_spool_dir,
    get_state_dir,
    get_state_path,
    load_state,
    save_state,
    utc_now,
)
from .daemon_log import daemon_log_paths, write_daemon_log
from .desktop_watcher import _OPENCODE_DB, evaluate_event
from .evidence_builder import EvidenceRunBuilder
from .report_browser import build_report_index


REQUIRED_OPENCODE_COLUMNS = {
    "session": {"id", "directory", "title", "time_created"},
    "part": {"session_id", "data", "time_created"},
}

CANONICAL_PROJECT_STATES = {
    "UNSUPERVISED",
    "FALLBACK_ONLY",
    "RUNNING",
    "BLOCKED",
    "REJECT",
    "ACCEPT_WITH_CONDITIONS",
    "ACCEPT",
}
LOCK_STALE_SECONDS = 120
STARTUP_FILENAME = "ai-gate-daemon.cmd"


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _seconds_since(value: str) -> float:
    parsed = _parse_iso(value)
    if parsed is None:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            process_query = 0x1000
            still_active = 259
            handle = kernel32.OpenProcess(process_query, False, pid)
            if not handle:
                return False
            exit_code = wintypes.DWORD()
            try:
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == still_active
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _update_daemon_info(
    updates: dict[str, Any],
    state_path: str | os.PathLike[str] | None = None,
) -> None:
    state = load_state(state_path)
    daemon_info = state.setdefault("daemon", {})
    daemon_info.update(updates)
    save_state(state, state_path)


def _connect_sqlite_readonly(path: str) -> sqlite3.Connection:
    db_path = Path(path).expanduser().resolve()
    uri = db_path.as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def check_opencode_compatibility(db_path: str | None = None) -> dict[str, Any]:
    """Return read-only compatibility details for the OpenCode SQLite DB."""
    path = str(Path(db_path or _OPENCODE_DB).expanduser())
    result: dict[str, Any] = {
        "path": path,
        "exists": os.path.isfile(path),
        "readable": False,
        "compatible": False,
        "status": "missing",
        "message": "OpenCode SQLite database not found.",
        "tables": {},
        "sample_tool_shape": "not_checked",
    }
    if not result["exists"]:
        return result

    try:
        db = _connect_sqlite_readonly(path)
        db.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        result.update({
            "status": "unreadable",
            "message": f"OpenCode SQLite database could not be opened read-only: {exc}",
        })
        return result

    try:
        result["readable"] = True
        missing: dict[str, list[str]] = {}
        for table, required_columns in REQUIRED_OPENCODE_COLUMNS.items():
            rows = db.execute(f"PRAGMA table_info({table})").fetchall()
            columns = {str(row["name"]) for row in rows}
            result["tables"][table] = sorted(columns)
            missing_columns = sorted(required_columns - columns)
            if missing_columns:
                missing[table] = missing_columns

        if missing:
            result.update({
                "status": "incompatible",
                "message": f"OpenCode DB schema missing required columns: {missing}",
                "missing_columns": missing,
            })
            return result

        sample = db.execute(
            "SELECT data FROM part WHERE data LIKE '%\"type\":\"tool\"%' OR data LIKE '%\"type\": \"tool\"%' "
            "ORDER BY time_created DESC LIMIT 1"
        ).fetchone()
        if sample:
            try:
                data = json.loads(sample["data"])
                state = data.get("state") if isinstance(data, dict) else None
                if (
                    isinstance(data, dict)
                    and data.get("type") == "tool"
                    and "tool" in data
                    and isinstance(state, dict)
                    and isinstance(state.get("input", {}), dict)
                ):
                    result["sample_tool_shape"] = "compatible"
                else:
                    result["sample_tool_shape"] = "unexpected"
                    result["sample_tool_keys"] = sorted(data.keys()) if isinstance(data, dict) else []
            except (json.JSONDecodeError, TypeError):
                result["sample_tool_shape"] = "malformed"
        else:
            result["sample_tool_shape"] = "no_tool_sample"

        result.update({
            "compatible": True,
            "status": "compatible",
            "message": "OpenCode SQLite schema has required tables and columns.",
        })
        return result
    except sqlite3.Error as exc:
        result.update({
            "status": "error",
            "message": f"OpenCode SQLite compatibility check failed: {exc}",
        })
        return result
    finally:
        db.close()


class SupervisorDaemon:
    def __init__(
        self,
        policy_dir: str,
        state_path: str | os.PathLike[str] | None = None,
        opencode_db: str | None = None,
        poll_interval: float = 2.0,
        idle_seconds: float = 300.0,
        validation_timeout: int = 120,
    ) -> None:
        self.policy_dir = policy_dir
        self.state_path = Path(state_path) if state_path else get_state_path()
        self.opencode_db = opencode_db or _OPENCODE_DB
        self.poll_interval = poll_interval
        self.idle_seconds = idle_seconds
        self.validation_timeout = validation_timeout
        self.stop_file = get_state_dir() / "daemon.stop"
        self.lock_file = get_state_dir() / "daemon.lock"
        self.owner_id = f"{os.getpid()}:{int(time.time() * 1000)}"
        self.state = load_state(self.state_path)

    def _save(self) -> None:
        save_state(self.state, self.state_path)

    def _log(self, event: str, level: str = "info", **fields: Any) -> None:
        try:
            write_daemon_log(event, level=level, state_dir=self.state_path.parent, **fields)
        except OSError:
            pass

    def _log_paths(self) -> dict[str, Path]:
        return daemon_log_paths(self.state_path.parent)

    def _lock_payload(self) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "owner_id": self.owner_id,
            "state_path": str(self.state_path),
            "heartbeat_at": utc_now(),
            "acquired_at": utc_now(),
        }

    def _read_lock(self) -> dict[str, Any]:
        if not self.lock_file.is_file():
            return {}
        try:
            data = json.loads(self.lock_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _lock_is_stale(self, lock: dict[str, Any]) -> bool:
        pid = int(lock.get("pid") or 0)
        heartbeat_at = str(lock.get("heartbeat_at") or lock.get("acquired_at") or "")
        if not _pid_running(pid):
            return True
        return _seconds_since(heartbeat_at) > max(LOCK_STALE_SECONDS, self.poll_interval * 10)

    def _acquire_lock(self) -> None:
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock = self._read_lock()
        if lock:
            same_owner = lock.get("owner_id") == self.owner_id
            if not same_owner and not self._lock_is_stale(lock):
                self._log(
                    "daemon-lock-held",
                    level="warning",
                    lock_path=str(self.lock_file),
                    owner_pid=lock.get("pid"),
                    owner_id=lock.get("owner_id"),
                )
                raise RuntimeError(f"ai-gate daemon is already running (pid {lock.get('pid')}).")
            if not same_owner:
                self._log(
                    "daemon-stale-lock-recovered",
                    level="warning",
                    lock_path=str(self.lock_file),
                    previous_pid=lock.get("pid"),
                    previous_owner_id=lock.get("owner_id"),
                )
        self.lock_file.write_text(json.dumps(self._lock_payload(), indent=2) + "\n", encoding="utf-8")

    def _release_lock(self) -> None:
        lock = self._read_lock()
        if lock.get("owner_id") == self.owner_id and self.lock_file.exists():
            try:
                self.lock_file.unlink()
            except OSError:
                pass

    def _heartbeat(self, status: str = "running") -> None:
        now = utc_now()
        lock = self._read_lock()
        if lock.get("owner_id") == self.owner_id:
            lock["heartbeat_at"] = now
            try:
                self.lock_file.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
            except OSError:
                pass
        _update_daemon_info({
            "pid": os.getpid(),
            "owner_id": self.owner_id,
            "status": status,
            "state_path": str(self.state_path),
            "lock_path": str(self.lock_file),
            "last_heartbeat_at": now,
            "log_jsonl_path": str(self._log_paths()["jsonl"]),
            "log_text_path": str(self._log_paths()["text"]),
        }, self.state_path)

    def _record_opencode_health(self) -> dict[str, Any]:
        health = check_opencode_compatibility(self.opencode_db)
        self.state.setdefault("daemon", {})["opencode"] = health
        self._save()
        if health.get("status") not in {"compatible", "missing"}:
            self._log(
                "opencode-adapter-health",
                level="warning",
                status=health.get("status"),
                message=health.get("message"),
                path=health.get("path"),
            )
        return health

    def _project_builder(self, record: dict[str, Any]) -> EvidenceRunBuilder:
        return EvidenceRunBuilder.from_existing(
            run_path=record["current_run_path"],
            project_dir=record["project_dir"],
            evidence_root=record["evidence_root"],
            task_type=record.get("task_type", "audit"),
            policy=record.get("policy", ""),
        )

    def _start_run(
        self,
        project_key: str,
        record: dict[str, Any],
        agent: str,
        session_id: str,
        late_snapshot: bool,
    ) -> EvidenceRunBuilder:
        if (
            record.get("current_run_path")
            and record.get("current_agent") == agent
            and record.get("current_session_id") == session_id
        ):
            return self._project_builder(record)

        if record.get("current_run_path"):
            self._finalize_project(project_key, reason="new-session")

        builder = EvidenceRunBuilder.create(
            project_dir=record["project_dir"],
            evidence_root=record["evidence_root"],
            task_type=record.get("task_type", "audit"),
            agent=agent,
            session_id=session_id,
            policy=record.get("policy", ""),
            late_snapshot=late_snapshot,
        )

        record.update({
            "current_run_id": builder.run_id,
            "current_run_path": str(builder.run_path),
            "current_agent": agent,
            "current_session_id": session_id,
            "latest_decision": "RUNNING",
            "latest_run_path": str(builder.run_path),
            "latest_agent": agent,
            "latest_session_id": session_id,
            "latest_next_action": "Daemon is collecting evidence.",
            "latest_updated_at": utc_now(),
            "latest_late_snapshot": late_snapshot,
            "latest_finalize_reason": "",
        })
        self.state["projects"][project_key] = record
        self._save()
        self._log(
            "run-started",
            project_dir=record.get("project_dir"),
            agent=agent,
            session_id=session_id,
            run_path=str(builder.run_path),
            late_snapshot=late_snapshot,
        )
        return builder

    def _finalize_project(self, project_key: str, reason: str = "finalize") -> dict[str, Any] | None:
        record = self.state.get("projects", {}).get(project_key)
        if not record or not record.get("current_run_path"):
            return None

        builder = self._project_builder(record)
        existing_metadata = _metadata_for_run(record.get("current_run_path", ""))
        report_path = Path(str(existing_metadata.get("acceptance_report_json", "")))
        reused = bool(
            existing_metadata.get("finalized_at")
            and existing_metadata.get("decision")
            and report_path.is_file()
        )
        if reused:
            metadata = existing_metadata
        else:
            metadata = builder.finalize(
                policy_dir=self.policy_dir,
                validation_timeout=self.validation_timeout,
            )
        record.update({
            "current_run_id": "",
            "current_run_path": "",
            "current_agent": "",
            "current_session_id": "",
            "latest_decision": metadata.get("decision", ""),
            "latest_run_path": str(builder.run_path),
            "latest_agent": builder.agent,
            "latest_session_id": builder.session_id,
            "latest_next_action": metadata.get("required_next_action", ""),
            "latest_updated_at": utc_now(),
            "latest_finalize_reason": reason,
            "latest_late_snapshot": bool(metadata.get("late_snapshot")),
        })
        self.state["projects"][project_key] = record
        self._save()
        try:
            build_report_index(record.get("project_dir", builder.project_dir))
        except Exception as exc:
            self._log(
                "report-index-error",
                level="warning",
                project_dir=record.get("project_dir", ""),
                error=str(exc),
            )
        self._log(
            "run-finalized",
            project_dir=record.get("project_dir"),
            agent=builder.agent,
            session_id=builder.session_id,
            run_path=str(builder.run_path),
            decision=metadata.get("decision", ""),
            finalize_reason=reason,
            reused_existing_report=reused,
        )
        return metadata

    def recover_running_projects(self) -> list[str]:
        finalized: list[str] = []
        for project_key, record in list(self.state.get("projects", {}).items()):
            if record.get("current_run_path") and Path(record["current_run_path"]).is_dir():
                self._finalize_project(project_key, reason="daemon-restart-recovery")
                finalized.append(project_key)
        if finalized:
            self._log(
                "recovery-finalized-runs",
                finalize_reason="daemon-restart-recovery",
                project_count=len(finalized),
            )
        return finalized

    def finalize_all(self, reason: str = "daemon-stop") -> None:
        for project_key in list(self.state.get("projects", {}).keys()):
            self._finalize_project(project_key, reason=reason)

    # OpenCode SQLite adapter

    def _connect_opencode(self) -> sqlite3.Connection | None:
        if not os.path.isfile(self.opencode_db):
            self._record_opencode_health()
            return None
        try:
            db = _connect_sqlite_readonly(self.opencode_db)
            db.row_factory = sqlite3.Row
            return db
        except sqlite3.Error:
            self._record_opencode_health()
            return None

    def _find_opencode_session(self, db: sqlite3.Connection, project_dir: str) -> str | None:
        candidates: list[str] = []
        p = str(Path(project_dir).resolve()).replace("\\", "/")
        for candidate in (p, os.path.dirname(p), os.path.dirname(os.path.dirname(p))):
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        for candidate in candidates:
            try:
                row = db.execute(
                    """SELECT id FROM session
                       WHERE directory = ?
                       ORDER BY time_created DESC LIMIT 1""",
                    (candidate,),
                ).fetchone()
            except sqlite3.Error:
                return None
            if row:
                return str(row["id"])
        return None

    def _opencode_row_count(self, db: sqlite3.Connection, session_id: str) -> int:
        try:
            row = db.execute("SELECT COUNT(*) AS c FROM part WHERE session_id = ?", (session_id,)).fetchone()
            return int(row["c"]) if row else 0
        except sqlite3.Error:
            return 0

    def _poll_opencode_rows(self, db: sqlite3.Connection, session_id: str, last_rowid: int) -> list[sqlite3.Row]:
        try:
            return list(db.execute(
                """SELECT rowid, time_created, data FROM part
                   WHERE session_id = ? AND rowid > ?
                   ORDER BY rowid""",
                (session_id, last_rowid),
            ).fetchall())
        except sqlite3.Error:
            return []

    def _opencode_event_from_row(self, session_id: str, row: sqlite3.Row) -> dict[str, Any] | None:
        try:
            data = json.loads(row["data"])
        except (json.JSONDecodeError, TypeError):
            return {
                "source": "opencode_sqlite",
                "agent": "opencode",
                "event_id": f"{session_id}:{row['rowid']}",
                "part_rowid": int(row["rowid"]),
                "timestamp": row["time_created"],
                "type": "adapter_event",
                "role": "tool",
                "tool_name": "opencode",
                "content": "Malformed OpenCode part row skipped.",
                "parse_failed": True,
                "violations": [{
                    "severity": "blocker",
                    "rule": "malformed-opencode-event",
                    "message": "OpenCode SQLite event could not be parsed as JSON.",
                }],
            }

        if data.get("type") != "tool":
            return None

        state = data.get("state") or {}
        tool_input = state.get("input") or {}
        event = {
            "source": "opencode_sqlite",
            "agent": "opencode",
            "event_id": f"{session_id}:{row['rowid']}",
            "part_rowid": int(row["rowid"]),
            "timestamp": row["time_created"],
            "type": "tool_result",
            "role": "tool",
            "session_id": session_id,
            "tool_name": data.get("tool", ""),
            "command": tool_input.get("command", "") or "",
            "file_path": tool_input.get("filePath", "") or tool_input.get("file_path", "") or "",
            "tool_input": tool_input,
            "content": state.get("output", "") or "",
            "status": state.get("status", "") or "",
        }
        event["violations"] = evaluate_event(event)
        return event

    def process_opencode(self) -> None:
        health = self._record_opencode_health()
        if not health.get("compatible"):
            return

        db = self._connect_opencode()
        if db is None:
            return
        try:
            for project_key, record in list(self.state.get("projects", {}).items()):
                if "opencode" not in record.get("agents", []):
                    continue

                session_id = self._find_opencode_session(db, record["project_dir"])
                if not session_id:
                    continue

                last_seen = record.setdefault("last_seen", {}).setdefault("opencode", {})
                finalized = set(last_seen.get("finalized_sessions", []))
                last_rowids = last_seen.setdefault("last_rowids", {})
                last_rowid = int(last_rowids.get(session_id, 0) or 0)
                rows = self._poll_opencode_rows(db, session_id, last_rowid)

                if session_id in finalized and not rows:
                    continue

                if not record.get("current_run_path"):
                    late_snapshot = self._opencode_row_count(db, session_id) > 0
                    self._start_run(project_key, record, "opencode", session_id, late_snapshot)
                    record = self.state["projects"][project_key]

                builder = self._project_builder(record)
                captured = 0
                max_rowid = last_rowid
                for row in rows:
                    max_rowid = max(max_rowid, int(row["rowid"]))
                    event = self._opencode_event_from_row(session_id, row)
                    if event is None:
                        continue
                    builder.append_event(event)
                    captured += 1

                last_rowids[session_id] = max_rowid
                if captured:
                    last_seen["last_event_at"] = utc_now()
                    record["latest_updated_at"] = utc_now()
                    record["latest_event_count"] = int(record.get("latest_event_count", 0) or 0) + captured
                elif record.get("current_run_path") and not last_seen.get("last_event_at"):
                    last_seen["last_event_at"] = utc_now()

                if (
                    record.get("current_run_path")
                    and record.get("current_agent") == "opencode"
                    and _seconds_since(last_seen.get("last_event_at", "")) >= self.idle_seconds
                ):
                    self._finalize_project(project_key, reason="opencode-idle-timeout")
                    record = self.state["projects"][project_key]
                    finalized = set(last_seen.get("finalized_sessions", []))
                    finalized.add(session_id)
                    record.setdefault("last_seen", {}).setdefault("opencode", {})["finalized_sessions"] = sorted(finalized)

                self.state["projects"][project_key] = record
            self._save()
        finally:
            db.close()

    # Codex hook spool adapter

    def _codex_spool_path(self) -> Path:
        return get_spool_dir() / "codex_events.jsonl"

    def _read_codex_events(self) -> tuple[list[dict[str, Any]], int]:
        records = self._read_codex_event_records()
        events = [event for event, _offset, _reason in records if event is not None]
        last_offset = records[-1][1] if records else 0
        return events, last_offset

    def _read_codex_event_records(self) -> list[tuple[dict[str, Any] | None, int, str]]:
        spool_path = self._codex_spool_path()
        if not spool_path.is_file():
            return []

        daemon_state = self.state.setdefault("daemon", {})
        offsets = daemon_state.setdefault("spool_offsets", {})
        offset = int(offsets.get(str(spool_path), 0) or 0)
        size = spool_path.stat().st_size
        if offset > size:
            offset = 0

        records: list[tuple[dict[str, Any] | None, int, str]] = []
        with spool_path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            while True:
                line = handle.readline()
                if not line:
                    break
                line_end = handle.tell()
                if not line.endswith("\n"):
                    break
                stripped = line.strip()
                if not stripped:
                    records.append((None, line_end, "blank"))
                    continue
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    records.append((None, line_end, "malformed-json"))
                    continue
                if isinstance(event, dict):
                    records.append((event, line_end, "ok"))
                else:
                    records.append((None, line_end, "non-object-json"))
        return records

    def _advance_codex_spool(self, offset: int) -> None:
        spool_path = self._codex_spool_path()
        offsets = self.state.setdefault("daemon", {}).setdefault("spool_offsets", {})
        offsets[str(spool_path)] = offset
        self._save()

    def process_codex_spool(self) -> None:
        records = self._read_codex_event_records()
        if not records:
            self._save()
            return

        for event, line_offset, skip_reason in records:
            if event is None:
                if skip_reason != "blank":
                    self._log(
                        "codex-spool-event-skipped",
                        level="warning",
                        reason=skip_reason,
                        offset=line_offset,
                    )
                self._advance_codex_spool(line_offset)
                continue

            cwd = event.get("cwd") or event.get("project_dir") or os.getcwd()
            project_key, record = find_project_for_cwd(cwd, self.state)
            if not project_key or not record:
                self._log(
                    "codex-spool-event-skipped",
                    level="warning",
                    reason="unregistered-project",
                    cwd=cwd,
                    offset=line_offset,
                )
                self._advance_codex_spool(line_offset)
                continue
            if "codex" not in record.get("agents", []):
                self._log(
                    "codex-spool-event-skipped",
                    level="warning",
                    reason="codex-agent-disabled",
                    project_dir=record.get("project_dir", ""),
                    offset=line_offset,
                )
                self._advance_codex_spool(line_offset)
                continue

            session_id = event.get("session_id") or "unknown"
            hook_event = event.get("hook_event_name") or event.get("hook_type") or ""
            event_key = (
                event.get("event_id")
                or f"{session_id}:{hook_event}:{event.get('tool_use_id', '')}:{event.get('timestamp', '')}"
            )

            last_seen = record.setdefault("last_seen", {}).setdefault("codex", {})
            processed = list(last_seen.get("processed_event_ids", []))
            if event_key in processed:
                self._advance_codex_spool(line_offset)
                continue

            if not record.get("current_run_path"):
                late_snapshot = hook_event != "SessionStart"
                self._start_run(project_key, record, "codex", session_id, late_snapshot)
                record = self.state["projects"][project_key]

            builder = self._project_builder(record)
            event.setdefault("source", "codex_hook")
            event.setdefault("agent", "codex")
            event.setdefault("event_id", event_key)
            event.setdefault("role", "tool")
            if hook_event in {"PostToolUse", "Stop"}:
                event.setdefault("type", "tool_result")
            builder.append_event(event)

            processed.append(event_key)
            last_seen["processed_event_ids"] = processed[-500:]
            last_seen["last_event_at"] = utc_now()
            record["latest_updated_at"] = utc_now()
            record["codex_hook_observed"] = True

            if hook_event == "Stop":
                self._finalize_project(project_key, reason="codex-stop-hook")
                record = self.state["projects"][project_key]

            self.state["projects"][project_key] = record
            self._advance_codex_spool(line_offset)

        self._save()

    def poll_once(self) -> None:
        self.state = load_state(self.state_path)
        self.process_opencode()
        self.process_codex_spool()

    def run_foreground(self, recover: bool = True) -> None:
        self.stop_file.parent.mkdir(parents=True, exist_ok=True)
        if self.stop_file.exists():
            self.stop_file.unlink()
        self._acquire_lock()
        _update_daemon_info({
            "pid": os.getpid(),
            "owner_id": self.owner_id,
            "status": "running",
            "started_at": utc_now(),
            "state_path": str(self.state_path),
            "lock_path": str(self.lock_file),
            "log_jsonl_path": str(self._log_paths()["jsonl"]),
            "log_text_path": str(self._log_paths()["text"]),
        }, self.state_path)
        self._log(
            "daemon-started",
            pid=os.getpid(),
            state_path=str(self.state_path),
            lock_path=str(self.lock_file),
            recover=recover,
        )
        self._heartbeat()

        try:
            self.state = load_state(self.state_path)
            if recover:
                self.recover_running_projects()
            while not self.stop_file.exists():
                try:
                    self.poll_once()
                except Exception as exc:
                    self._log("daemon-poll-error", level="error", error=str(exc))
                    _update_daemon_info({"last_error": str(exc), "last_error_at": utc_now()}, self.state_path)
                self._heartbeat()
                time.sleep(self.poll_interval)
        finally:
            self.state = load_state(self.state_path)
            self.finalize_all(reason="daemon-stop")
            _update_daemon_info({
                "pid": os.getpid(),
                "status": "stopped",
                "stopped_at": utc_now(),
                "state_path": str(self.state_path),
            }, self.state_path)
            self._log("daemon-stopped", pid=os.getpid(), state_path=str(self.state_path))
            if self.stop_file.exists():
                try:
                    self.stop_file.unlink()
                except OSError:
                    pass
            self._release_lock()


def daemon_owner_status(state_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    root = Path(state_dir) if state_dir else get_state_dir()
    lock_path = root / "daemon.lock"
    result: dict[str, Any] = {
        "lock_path": str(lock_path),
        "lock_exists": lock_path.is_file(),
        "pid": None,
        "owner_id": "",
        "running": False,
        "stale": False,
        "heartbeat_at": "",
    }
    if not lock_path.is_file():
        return result
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        result["stale"] = True
        return result
    if not isinstance(lock, dict):
        result["stale"] = True
        return result
    pid = int(lock.get("pid") or 0)
    heartbeat_at = str(lock.get("heartbeat_at") or lock.get("acquired_at") or "")
    running = _pid_running(pid)
    stale = (not running) or _seconds_since(heartbeat_at) > LOCK_STALE_SECONDS
    result.update({
        "pid": pid or None,
        "owner_id": str(lock.get("owner_id") or ""),
        "running": running,
        "stale": stale,
        "heartbeat_at": heartbeat_at,
    })
    return result


def start_background(
    interval: float = 2.0,
    idle_seconds: float = 300.0,
    validation_timeout: int = 120,
    policy_dir: str | None = None,
    recover: bool = True,
) -> int:
    owner = daemon_owner_status()
    if owner.get("running") and not owner.get("stale") and owner.get("pid"):
        write_daemon_log(
            "daemon-start-skipped-already-running",
            state_dir=get_state_dir(),
            pid=owner.get("pid"),
            lock_path=owner.get("lock_path"),
        )
        return int(owner["pid"])

    cmd = [
        sys.executable,
        "-m",
        "prove_it_ai_gate.cli",
        "daemon",
        "start",
        "--foreground",
        "--interval",
        str(interval),
        "--idle-seconds",
        str(idle_seconds),
        "--validation-timeout",
        str(validation_timeout),
    ]
    if policy_dir:
        cmd.extend(["--policy-dir", policy_dir])
    if not recover:
        cmd.append("--no-recover")
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    proc = subprocess.Popen(cmd, **kwargs)
    _update_daemon_info({
        "pid": proc.pid,
        "status": "starting",
        "started_at": utc_now(),
        "state_path": str(get_state_path()),
        "log_jsonl_path": str(daemon_log_paths()["jsonl"]),
        "log_text_path": str(daemon_log_paths()["text"]),
    })
    write_daemon_log("daemon-background-started", pid=proc.pid, state_path=str(get_state_path()))
    return proc.pid


def request_stop() -> Path:
    stop_file = get_state_dir() / "daemon.stop"
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stop_file.write_text(utc_now() + "\n", encoding="utf-8")
    return stop_file


def _codex_hooks_configured(project_dir: str) -> bool:
    hooks_path = Path(project_dir) / ".codex" / "hooks.json"
    if not hooks_path.is_file():
        return False
    try:
        text = hooks_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "prove_it_ai_gate.codex_hooks" in text


def _metadata_for_run(path: str) -> dict[str, Any]:
    if not path:
        return {}
    metadata_path = Path(path) / "metadata.json"
    if not metadata_path.is_file():
        return {}
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _project_next_action(state: str, coverage: str, existing: str) -> str:
    if existing:
        return existing
    if state == "RUNNING":
        return "Wait for the daemon to finalize the current run, or run ai-gate daemon stop to finalize now."
    if state == "BLOCKED":
        return "Resolve the blocker in the latest acceptance report, then run a new supervised session."
    if state == "REJECT":
        return "Review the failed checks in the latest acceptance report and fix the rejected evidence."
    if state == "ACCEPT_WITH_CONDITIONS":
        return "Review and satisfy the report conditions before treating the run as fully accepted."
    if state == "FALLBACK_ONLY":
        return "Run ai-gate setup codex, trust hooks through /hooks, and collect a hook-observed run."
    if state == "UNSUPERVISED" and coverage == "HOOKS_CONFIGURED_UNTRUSTED_UNVERIFIED":
        return "Start or restart Codex, open /hooks, review and trust the ai-gate hook, then run a session."
    if state == "UNSUPERVISED":
        return "Register the project and configure a supported agent hook or OpenCode daemon supervision."
    return ""


def status_rows(
    state_path: str | os.PathLike[str] | None = None,
    opencode_db: str | None = None,
) -> list[dict[str, Any]]:
    state = load_state(state_path)
    daemon_info = state.get("daemon", {})
    opencode_health = daemon_info.get("opencode") or check_opencode_compatibility(opencode_db)
    owner = daemon_owner_status(Path(state_path).parent if state_path else None)
    log_paths = daemon_log_paths(Path(state_path).parent if state_path else None)
    rows: list[dict[str, Any]] = []
    for key, record in sorted(state.get("projects", {}).items()):
        run_path = record.get("current_run_path") or record.get("latest_run_path", "")
        metadata = _metadata_for_run(run_path)
        last_event_at = (record.get("last_seen", {}).get("opencode", {}) or {}).get("last_event_at", "")
        codex_coverage = ""
        if "codex" in record.get("agents", []):
            if metadata.get("fallback_only"):
                codex_coverage = "FALLBACK_ONLY"
            elif record.get("codex_hook_observed"):
                codex_coverage = "HOOKS_OBSERVED"
            elif record.get("codex_fallback_observed"):
                codex_coverage = "FALLBACK_ONLY"
            elif _codex_hooks_configured(record.get("project_dir", "")):
                codex_coverage = "HOOKS_CONFIGURED_UNTRUSTED_UNVERIFIED"
            else:
                codex_coverage = "UNSUPERVISED"
        if record.get("current_run_path"):
            project_state = "RUNNING"
        else:
            latest_decision = record.get("latest_decision") or ""
            if latest_decision in CANONICAL_PROJECT_STATES:
                project_state = latest_decision
            elif metadata.get("fallback_only") or record.get("codex_fallback_observed"):
                project_state = "FALLBACK_ONLY"
            else:
                project_state = "UNSUPERVISED"
        coverage = codex_coverage or "UNSUPERVISED"
        next_action = _project_next_action(project_state, coverage, record.get("latest_next_action", ""))
        rows.append({
            "key": key,
            "project_dir": record.get("project_dir", ""),
            "state": project_state,
            "status": project_state,
            "coverage": coverage,
            "agent": record.get("current_agent") or record.get("latest_agent", ""),
            "session_id": record.get("current_session_id") or record.get("latest_session_id", ""),
            "decision": "" if project_state == "RUNNING" else record.get("latest_decision", ""),
            "latest_run_path": run_path,
            "required_next_action": next_action,
            "next_action": next_action,
            "codex_coverage": codex_coverage,
            "late_snapshot": bool(metadata.get("late_snapshot", record.get("latest_late_snapshot", False))),
            "finalize_reason": record.get("latest_finalize_reason", ""),
            "last_event_at": last_event_at,
            "idle_for_seconds": round(_seconds_since(last_event_at), 1) if project_state == "RUNNING" and last_event_at else None,
            "daemon_status": daemon_info.get("status", "unknown"),
            "daemon_pid": daemon_info.get("pid"),
            "daemon_heartbeat_at": daemon_info.get("last_heartbeat_at", ""),
            "daemon_lock_path": owner.get("lock_path", ""),
            "daemon_lock_stale": bool(owner.get("stale")),
            "logs": str(log_paths["text"]),
            "log_jsonl_path": str(log_paths["jsonl"]),
            "opencode_db_status": opencode_health.get("status", "unknown"),
            "opencode_db_message": opencode_health.get("message", ""),
            "opencode_db_path": opencode_health.get("path", opencode_db or _OPENCODE_DB),
            "opencode_sample_tool_shape": opencode_health.get("sample_tool_shape", ""),
        })
    return rows


def _windows_startup_dir() -> Path:
    if os.name != "nt":
        raise RuntimeError("Windows startup integration is only available on Windows.")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set; cannot locate Windows Startup folder.")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _startup_command_text() -> str:
    paths = daemon_log_paths()
    log_dir = paths["text"].parent
    log_path = paths["text"]
    return (
        "@echo off\r\n"
        f'if not exist "{log_dir}" mkdir "{log_dir}"\r\n'
        f'"{sys.executable}" -m prove_it_ai_gate.cli daemon start >> "{log_path}" 2>&1\r\n'
    )


def startup_status() -> dict[str, Any]:
    startup = _windows_startup_dir()
    path = startup / STARTUP_FILENAME
    expected = _startup_command_text()
    exists = path.is_file()
    current = ""
    if exists:
        try:
            current = path.read_bytes().decode("utf-8")
        except OSError:
            current = ""
    status = "installed" if exists and current == expected else "stale_command" if exists else "missing"
    return {
        "status": status,
        "installed": status == "installed",
        "path": str(path),
        "startup_dir": str(startup),
        "expected_command": expected,
        "current_command": current,
        "log_path": str(daemon_log_paths()["text"]),
    }


def _validate_startup_python() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import prove_it_ai_gate.cli"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError("Unable to import prove_it_ai_gate.cli with the current Python executable.")


def install_windows_startup(force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    status = startup_status()
    path = Path(status["path"])
    if dry_run:
        status.update({"changed": False, "dry_run": True})
        return status

    _validate_startup_python()
    path.parent.mkdir(parents=True, exist_ok=True)
    if status["status"] == "installed":
        status.update({"changed": False, "dry_run": False})
        write_daemon_log("startup-install-skipped", status="installed", path=str(path))
        return status
    if status["status"] == "stale_command" and not force:
        status.update({"changed": False, "dry_run": False})
        write_daemon_log("startup-install-stale", level="warning", path=str(path))
        return status

    path.write_bytes(status["expected_command"].encode("utf-8"))
    installed = startup_status()
    installed.update({"changed": True, "dry_run": False})
    write_daemon_log("startup-installed", path=str(path), force=force)
    return installed


def uninstall_windows_startup() -> dict[str, Any]:
    path = _windows_startup_dir() / STARTUP_FILENAME
    existed = path.exists()
    if path.exists():
        path.unlink()
    write_daemon_log("startup-uninstalled", path=str(path), existed=existed)
    return {
        "status": "removed" if existed else "absent",
        "path": str(path),
        "changed": existed,
        "installed": False,
    }
