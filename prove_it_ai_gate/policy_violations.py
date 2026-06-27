from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import CheckResult, Issue, Severity


BLOCKER_LEVELS = {"blocker", "high", "critical", "deny", "blocked"}
WARNING_LEVELS = {"warning", "warn", "medium"}


def _load_events(transcript_path: str) -> list[dict[str, Any]]:
    path = Path(transcript_path)
    if not path.is_file():
        return []

    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line_num, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                event["_line"] = line_num
                events.append(event)
    return events


def check_transcript_policy_violations(transcript_path: str) -> CheckResult:
    """Reject or warn when captured tool events contain policy violations."""
    issues: list[Issue] = []
    events = _load_events(transcript_path)

    for event in events:
        fail_open = bool(event.get("fail_open") or event.get("capture_failed") or event.get("parse_failed"))
        if fail_open:
            issues.append(Issue(
                "policy_violation_check",
                Severity.BLOCKER,
                f"Line {event.get('_line', '?')}: hook or adapter failed open; evidence coverage is incomplete",
            ))

        violations = event.get("violations") or []
        if not isinstance(violations, list):
            continue

        for violation in violations:
            if not isinstance(violation, dict):
                continue
            severity = str(violation.get("severity") or "").lower()
            message = str(violation.get("message") or violation.get("rule") or "policy violation")
            line = event.get("_line", "?")
            if severity in BLOCKER_LEVELS:
                issues.append(Issue(
                    "policy_violation_check",
                    Severity.REJECT,
                    f"Line {line}: {message}",
                ))
            elif severity in WARNING_LEVELS:
                issues.append(Issue(
                    "policy_violation_check",
                    Severity.WARNING,
                    f"Line {line}: {message}",
                ))

    status = "passed"
    if any(i.severity == Severity.BLOCKER for i in issues):
        status = "blocked"
    elif any(i.severity == Severity.REJECT for i in issues):
        status = "failed"
    elif any(i.severity == Severity.WARNING for i in issues):
        status = "warning"

    return CheckResult(
        check_name="policy_violation_check",
        status=status,
        issues=issues,
        details={"events_checked": len(events)},
    )
