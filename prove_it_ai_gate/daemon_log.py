from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .daemon_state import get_state_dir, utc_now


REDACTED_KEYS = {
    "content",
    "raw_event",
    "stderr",
    "stdout",
    "tool_output",
    "tool_response",
    "transcript",
    "transcript_body",
}


def daemon_log_dir(state_dir: str | Path | None = None) -> Path:
    return Path(state_dir) / "logs" if state_dir else get_state_dir() / "logs"


def daemon_log_paths(state_dir: str | Path | None = None) -> dict[str, Path]:
    root = daemon_log_dir(state_dir)
    return {
        "jsonl": root / "daemon.jsonl",
        "text": root / "daemon.log",
    }


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return redact_log_fields(value)
    if isinstance(value, list):
        return [_safe_value(item) for item in value[:50]]
    if isinstance(value, str) and len(value) > 500:
        return value[:497] + "..."
    return value


def redact_log_fields(fields: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in fields.items():
        if key.lower() in REDACTED_KEYS:
            redacted[key] = "[redacted]"
        else:
            redacted[key] = _safe_value(value)
    return redacted


def write_daemon_log(
    event: str,
    level: str = "info",
    state_dir: str | Path | None = None,
    **fields: Any,
) -> dict[str, Any]:
    paths = daemon_log_paths(state_dir)
    paths["jsonl"].parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": utc_now(),
        "level": level,
        "event": event,
        **redact_log_fields(fields),
    }

    with paths["jsonl"].open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    details = " ".join(
        f"{key}={value}"
        for key, value in entry.items()
        if key not in {"timestamp", "level", "event"}
        and value not in ("", None, [], {})
    )
    line = f"{entry['timestamp']} {level.upper()} {event}"
    if details:
        line += f" {details}"
    with paths["text"].open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return entry


def read_daemon_logs(
    tail: int = 50,
    json_output: bool = False,
    state_dir: str | Path | None = None,
) -> list[str] | list[dict[str, Any]]:
    paths = daemon_log_paths(state_dir)
    path = paths["jsonl"] if json_output else paths["text"]
    if not path.is_file():
        return []

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if tail > 0:
        lines = lines[-tail:]

    if not json_output:
        return lines

    entries: list[dict[str, Any]] = []
    for line in lines:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            entries.append({"parse_error": True, "line": line})
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries
