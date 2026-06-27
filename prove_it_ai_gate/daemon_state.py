from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATE_VERSION = 1
DEFAULT_AGENTS = ["opencode", "codex"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_state_dir() -> Path:
    override = os.environ.get("AI_GATE_DAEMON_HOME") or os.environ.get("AI_GATE_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()

    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "prove-it-ai-gate"

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "prove-it-ai-gate"
    return Path.home() / ".config" / "prove-it-ai-gate"


def get_state_path() -> Path:
    return get_state_dir() / "daemon_state.json"


def get_spool_dir() -> Path:
    override = os.environ.get("AI_GATE_SPOOL_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return get_state_dir() / "spool"


def normalize_project_path(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def _default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "daemon": {},
        "projects": {},
    }


def load_state(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    state_path = Path(path) if path else get_state_path()
    if not state_path.is_file():
        return _default_state()

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_state()

    if not isinstance(data, dict):
        return _default_state()

    data.setdefault("version", STATE_VERSION)
    data.setdefault("created_at", utc_now())
    data.setdefault("updated_at", utc_now())
    data.setdefault("daemon", {})
    data.setdefault("projects", {})
    return data


def save_state(state: dict[str, Any], path: str | os.PathLike[str] | None = None) -> Path:
    state_path = Path(path) if path else get_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state["version"] = STATE_VERSION
    state["updated_at"] = utc_now()

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{state_path.name}.",
        suffix=".tmp",
        dir=str(state_path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp_name, state_path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    return state_path


def default_evidence_root(project_dir: str) -> str:
    return str(Path(project_dir) / "evidence")


def make_project_record(
    project_dir: str,
    task_type: str = "audit",
    evidence_root: str | None = None,
    policy: str = "",
    agents: list[str] | None = None,
) -> dict[str, Any]:
    resolved = str(Path(project_dir).expanduser().resolve())
    return {
        "project_dir": resolved,
        "task_type": task_type or "audit",
        "evidence_root": str(Path(evidence_root).expanduser().resolve()) if evidence_root else default_evidence_root(resolved),
        "policy": policy or "",
        "agents": agents or list(DEFAULT_AGENTS),
        "registered_at": utc_now(),
        "current_run_id": "",
        "current_run_path": "",
        "current_agent": "",
        "current_session_id": "",
        "latest_decision": "",
        "latest_run_path": "",
        "latest_agent": "",
        "latest_session_id": "",
        "latest_next_action": "",
        "latest_updated_at": "",
        "last_seen": {},
    }


def register_project(
    project_dir: str,
    task_type: str = "audit",
    evidence_root: str | None = None,
    policy: str = "",
    agents: list[str] | None = None,
    state_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    state = load_state(state_path)
    key = normalize_project_path(project_dir)
    record = state["projects"].get(key, {})
    new_record = make_project_record(project_dir, task_type, evidence_root, policy, agents)

    if record:
        new_record["registered_at"] = record.get("registered_at") or new_record["registered_at"]
        for field in (
            "current_run_id",
            "current_run_path",
            "current_agent",
            "current_session_id",
            "latest_decision",
            "latest_run_path",
            "latest_agent",
            "latest_session_id",
            "latest_next_action",
            "latest_updated_at",
            "last_seen",
        ):
            new_record[field] = record.get(field, new_record.get(field))

    state["projects"][key] = new_record
    save_state(state, state_path)
    return new_record


def unregister_project(
    project_dir: str,
    state_path: str | os.PathLike[str] | None = None,
) -> bool:
    state = load_state(state_path)
    key = normalize_project_path(project_dir)
    existed = key in state["projects"]
    if existed:
        del state["projects"][key]
        save_state(state, state_path)
    return existed


def list_projects(state_path: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    state = load_state(state_path)
    return [dict(record, key=key) for key, record in sorted(state["projects"].items())]


def find_project_for_cwd(
    cwd: str | os.PathLike[str],
    state: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    state = state or load_state()
    cwd_path = Path(cwd).expanduser().resolve()
    best_key: str | None = None
    best_record: dict[str, Any] | None = None
    best_len = -1

    for key, record in state.get("projects", {}).items():
        try:
            project_path = Path(record.get("project_dir") or key).expanduser().resolve()
            is_match = cwd_path == project_path or project_path in cwd_path.parents
        except OSError:
            is_match = False
        if is_match and len(str(project_path)) > best_len:
            best_key = key
            best_record = record
            best_len = len(str(project_path))

    if best_key is None or best_record is None:
        return None, None
    return best_key, best_record


def update_project_record(
    project_key: str,
    updates: dict[str, Any],
    state_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    state = load_state(state_path)
    if project_key not in state["projects"]:
        raise KeyError(f"Project not registered: {project_key}")
    state["projects"][project_key].update(updates)
    save_state(state, state_path)
    return state["projects"][project_key]


def set_daemon_info(
    info: dict[str, Any],
    state_path: str | os.PathLike[str] | None = None,
) -> None:
    state = load_state(state_path)
    state["daemon"] = info
    save_state(state, state_path)
