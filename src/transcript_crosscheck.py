from __future__ import annotations

import json
from pathlib import Path

from .types import CheckResult, Issue, Severity

WRITE_TOOLS = {"write", "edit", "create"}
MODIFY_COMMANDS = ["mkdir", "touch", "New-Item", "Set-Content", "Add-Content", "Out-File",
                   "write_text", "write_bytes", "echo >", "echo >>", "tee "]


def _parse_transcript_events(transcript_path: str) -> list[dict]:
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
                pass
    return events


def _extract_write_events(events: list[dict]) -> list[dict]:
    writes: list[dict] = []
    for event in events:
        tool = event.get("tool_name", "")
        event_type = event.get("type", "")
        if tool.lower() in WRITE_TOOLS or event_type == "write":
            writes.append(event)
            continue

        command = event.get("command", "")
        command_lower = command.lower()
        if any(marker in command_lower for marker in MODIFY_COMMANDS):
            writes.append(event)
    return writes


def _read_closeout(evidence_path: str) -> str:
    closeout = Path(evidence_path) / "closeout.md"
    if closeout.is_file():
        return closeout.read_text(encoding="utf-8", errors="replace").lower()
    return ""


def _claims_no_modifications(closeout: str) -> bool:
    phrases = [
        "no files changed", "no changes", "no modifications",
        "no files were modified", "did not modify",
        "no workspace changes", "read-only", "no files created",
    ]
    return any(phrase in closeout for phrase in phrases)


def check_closeout_vs_transcript(evidence_path: str, transcript_path: str) -> CheckResult:
    issues: list[Issue] = []

    closeout = _read_closeout(evidence_path)
    events = _parse_transcript_events(transcript_path)

    if not closeout:
        return CheckResult(
            check_name="closeout_transcript_crosscheck",
            status="warning",
            issues=[Issue("closeout_transcript_crosscheck", Severity.WARNING,
                          "No closeout.md found for cross-check")],
        )

    if not events:
        return CheckResult(
            check_name="closeout_transcript_crosscheck",
            status="warning",
            issues=[Issue("closeout_transcript_crosscheck", Severity.WARNING,
                          "No transcript events found for cross-check")],
        )

    write_events = _extract_write_events(events)
    claims_clean = _claims_no_modifications(closeout)

    if claims_clean and write_events:
        for we in write_events:
            tool = we.get("tool_name", we.get("type", "unknown"))
            command = we.get("command", "")[:80]
            issues.append(Issue(
                "closeout_transcript_crosscheck",
                Severity.REJECT,
                f"Closeout claims no modifications but transcript shows write: "
                f"line {we.get('_line', '?')}, tool={tool}, cmd={command}",
            ))

    status = "passed"
    if any(i.severity == Severity.REJECT for i in issues):
        status = "failed"
    elif any(i.severity == Severity.WARNING for i in issues):
        status = "warning"

    return CheckResult(
        check_name="closeout_transcript_crosscheck",
        status=status,
        issues=issues,
        details={
            "write_events_found": len(write_events),
            "closeout_claims_no_modifications": claims_clean,
        },
    )
