from __future__ import annotations

import json
import re
from pathlib import Path

from .types import CheckResult, Issue, Severity

TRUNCATION_MARKERS = [
    "truncated",
    "output omitted",
    "showing first",
    "too many results",
    "partial results",
    "max output",
    "results omitted",
]

TRUNCATION_PATTERN = re.compile(
    "|".join(re.escape(marker) for marker in TRUNCATION_MARKERS),
    re.IGNORECASE,
)


def parse_transcript(transcript_path: str) -> list[dict]:
    path = Path(transcript_path)
    if not path.is_file():
        return []

    events: list[dict] = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line_num, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                event["_line"] = line_num
                events.append(event)
            except json.JSONDecodeError:
                events.append({
                    "_line": line_num,
                    "_malformed": True,
                    "_raw": line[:500],
                })
    return events


def get_event_text(event: dict) -> str:
    candidates = ["content", "stdout", "stderr", "output", "result"]
    texts: list[str] = []
    for key in candidates:
        val = event.get(key)
        if isinstance(val, str):
            texts.append(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    texts.append(item)
                elif isinstance(item, dict):
                    for sub_key in candidates:
                        sub_val = item.get(sub_key)
                        if isinstance(sub_val, str):
                            texts.append(sub_val)
    return "\n".join(texts)


def is_user_or_assistant_discussion(event: dict) -> bool:
    role = event.get("role", "")
    if role in ("user", "assistant"):
        return True
    event_type = event.get("type", "")
    if event_type in ("message", "text", "reasoning"):
        return True
    return False


def check_transcript_truncation(transcript_path: str) -> CheckResult:
    events = parse_transcript(transcript_path)
    issues: list[Issue] = []
    truncated_steps: list[dict] = []
    replacement_artifacts: list[str] = []

    malformed_count = sum(1 for e in events if e.get("_malformed"))
    if malformed_count > 0:
        if malformed_count == len(events):
            issues.append(Issue(
                "transcript_truncation_check",
                Severity.BLOCKER,
                f"Transcript is entirely malformed ({malformed_count} lines are not valid JSONL)",
            ))
        else:
            issues.append(Issue(
                "transcript_truncation_check",
                Severity.WARNING,
                f"Transcript has {malformed_count} malformed line(s)",
            ))

    if not events:
        issues.append(Issue(
            "transcript_truncation_check",
            Severity.BLOCKER,
            "Transcript is empty or missing",
        ))
        return CheckResult(
            check_name="transcript_truncation_check",
            status="blocked",
            issues=issues,
            details={"truncated_steps": [], "replacement_artifacts": [], "total_events": 0},
        )

    for event in events:
        if event.get("_malformed"):
            continue

        if is_user_or_assistant_discussion(event):
            continue

        text = get_event_text(event)
        if not text:
            continue

        match = TRUNCATION_PATTERN.search(text)
        if not match:
            continue

        truncated_steps.append({
            "line": event.get("_line"),
            "tool": event.get("tool_name", event.get("type", "unknown")),
            "marker": match.group(0),
        })

        has_replacement = _has_replacement_after(events, event)
        if not has_replacement:
            issues.append(Issue(
                "transcript_truncation_check",
                Severity.BLOCKER,
                f"Line {event['_line']}: Tool output contains '{match.group(0)}' "
                f"with no replacement artifact found afterward",
                evidence=f"Marker: {match.group(0)}",
            ))
        else:
            replacement_artifacts.append(f"Line {event['_line']}: replacement found for '{match.group(0)}'")

    status = "passed" if not any(i.severity == Severity.BLOCKER for i in issues) else "blocked"
    return CheckResult(
        check_name="transcript_truncation_check",
        status=status,
        issues=issues,
        details={
            "truncated_steps": truncated_steps,
            "replacement_artifacts": replacement_artifacts,
            "total_events": len(events),
            "malformed_lines": malformed_count,
        },
    )


def _has_replacement_after(events: list[dict], truncated_event: dict) -> bool:
    truncated_line = truncated_event.get("_line", 0)
    truncated_tool = truncated_event.get("tool_name", "")

    for event in events:
        if event.get("_line", 0) <= truncated_line:
            continue
        if event.get("_malformed"):
            continue

        if event.get("tool_name") == truncated_tool:
            text = get_event_text(event)
            if text and len(text) > 20 and not TRUNCATION_PATTERN.search(text):
                return True

        if event.get("type") == "tool_result" and event.get("tool_name") == truncated_tool:
            text = get_event_text(event)
            if text and len(text) > 20 and not TRUNCATION_PATTERN.search(text):
                return True

    return False
