from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .gate_engine import AcceptanceReport


CAPTURE_TEMPLATE = """---
type: project_learning
id: {capture_id}
status: draft
owner: agent
last_validated: {date}
tags: [{tags}]
source_task: {task_type}
source_policy: {policy_name}
source_decision: {decision}
---

# {title}

## Project

{repo_path}

## Context

Task type: `{task_type}`
Policy: `{policy_name}`
Decision: `{decision}`
Generated: {generated_at}

## What Worked

{what_worked}

## What Did Not Work

{what_did_not_work}

## Reusable Insight

{reusable_insight}

## Evidence Summary

{evidence_summary}
"""


def _generate_capture_id() -> str:
    return f"capture-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def _safe_read(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def extract_what_worked(report: AcceptanceReport, evidence_root: Path) -> str:
    passed_checks = [c.check_name for c in report.checks if c.status == "passed"]
    if passed_checks:
        return f"The following checks passed: {', '.join(passed_checks)}."
    closeout = _safe_read(evidence_root / "closeout.md")
    if closeout:
        lines = closeout.splitlines()[:5]
        return " ".join(l for l in lines if not l.startswith("#") and l.strip())
    return "No specific successes recorded."


def extract_what_did_not_work(report: AcceptanceReport, evidence_root: Path) -> str:
    failures: list[str] = []
    for check in report.checks:
        for issue in check.issues:
            failures.append(f"- [{issue.severity.value}] {check.check_name}: {issue.message}")

    if failures:
        return "\n".join(failures)

    closeout = _safe_read(evidence_root / "closeout.md")
    if closeout:
        return "See closeout for details."
    return "No specific failures recorded."


def extract_reusable_insight(report: AcceptanceReport, evidence_root: Path) -> str:
    reason = report.required_next_action

    if report.decision.value in ("REJECT", "BLOCKED"):
        checks_triggered = report.failed_checks + report.blocked_checks
        return (
            f"This task was {report.decision.value}. "
            f"Checks that caught the failure: {', '.join(checks_triggered)}. "
            f"Required action: {reason}"
        )

    if report.decision.value == "ACCEPT_WITH_CONDITIONS":
        return (
            f"Task was accepted with conditions. "
            f"Warnings: {', '.join(report.warning_checks)}. "
            f"Action: {reason}"
        )

    return f"Task was accepted cleanly. All checks passed under policy '{report.policy_name}'."


def extract_evidence_summary(report: AcceptanceReport) -> str:
    total = len(report.checks)
    passed = sum(1 for c in report.checks if c.status == "passed")
    failed = sum(1 for c in report.checks if c.status == "failed")
    blocked = sum(1 for c in report.checks if c.status == "blocked")
    warning = sum(1 for c in report.checks if c.status == "warning")

    parts = [f"{total} checks total"]
    if passed:
        parts.append(f"{passed} passed")
    if failed:
        parts.append(f"{failed} failed")
    if blocked:
        parts.append(f"{blocked} blocked")
    if warning:
        parts.append(f"{warning} warnings")

    return ", ".join(parts)


def extract_tags(report: AcceptanceReport) -> str:
    tags = ["ai-gate", "captured", report.task_type]
    if report.decision.value == "REJECT":
        tags.append("failure")
    elif report.decision.value == "BLOCKED":
        tags.append("blocked")
    elif report.decision.value == "ACCEPT_WITH_CONDITIONS":
        tags.append("conditional")
    return ", ".join(tags)


def generate_title(report: AcceptanceReport) -> str:
    if report.decision.value in ("REJECT", "BLOCKED"):
        return f"Learning: {report.decision.value} — {report.task_type} under {report.policy_name}"
    return f"Learning: {report.task_type} passed under {report.policy_name}"


def generate_capture(report: AcceptanceReport, evidence_path: str) -> str:
    evidence_root = Path(evidence_path)
    capture_id = _generate_capture_id()
    date = datetime.now().date().isoformat()

    return CAPTURE_TEMPLATE.format(
        capture_id=capture_id,
        date=date,
        tags=extract_tags(report),
        task_type=report.task_type,
        policy_name=report.policy_name,
        decision=report.decision.value,
        title=generate_title(report),
        repo_path=report.repo_path,
        generated_at=report.generated_at,
        what_worked=extract_what_worked(report, evidence_root),
        what_did_not_work=extract_what_did_not_work(report, evidence_root),
        reusable_insight=extract_reusable_insight(report, evidence_root),
        evidence_summary=extract_evidence_summary(report),
    )


def write_capture(report: AcceptanceReport, evidence_path: str, output_dir: str) -> Path:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    capture_id = _generate_capture_id()
    filename = f"{capture_id}.md"
    capture_path = out_dir / filename

    content = generate_capture(report, evidence_path)
    capture_path.write_text(content, encoding="utf-8")

    return capture_path


def upload_to_wiki(capture_path: str, wiki_url: str, token_id: str, token_secret: str,
                   book_name: str = "Project Learnings", chapter_name: str = "Captures") -> dict:
    import json
    import urllib.error
    import urllib.request

    content = Path(capture_path).read_text(encoding="utf-8")
    title = Path(capture_path).stem.replace("-", " ").title()

    base = wiki_url.rstrip("/")

    def api(method: str, path: str, payload: dict | None = None, timeout: int = 30) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{base}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Token {token_id}:{token_secret}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 2:
                    time.sleep(5)
                    continue
                raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:200]}")
            except urllib.error.URLError as exc:
                if attempt < 2:
                    time.sleep(3)
                    continue
                raise RuntimeError(f"Connection failed: {exc.reason}")
        raise RuntimeError("Failed after retries")

    import re
    def slug(v: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", v.lower()).strip("-")

    import time as _time

    books = api("GET", "/api/books")
    book = next((b for b in books.get("data", []) if b.get("name") == book_name), None)
    if not book:
        book = api("POST", "/api/books", {"name": book_name, "description": "Auto-captured AI gate learnings."})

    chapters = api("GET", "/api/chapters")
    chapter = next((c for c in chapters.get("data", [])
                    if c.get("book_id") == book["id"] and c.get("name") == chapter_name), None)
    if not chapter:
        chapter = api("POST", "/api/chapters", {"book_id": book["id"], "name": chapter_name,
                                                  "description": "Auto-captured learning pages."})

    pages = api("GET", "/api/pages")
    existing = next((p for p in pages.get("data", [])
                     if p.get("chapter_id") == chapter["id"] and p.get("name") == title), None)

    if existing:
        result = api("PUT", f"/api/pages/{existing['id']}", {"name": title, "markdown": content,
                                                               "summary": "Updated AI gate capture."})
        return {"status": "updated", "page_id": result.get("id"), "title": title}
    else:
        result = api("POST", "/api/pages", {"chapter_id": chapter["id"], "name": title, "markdown": content,
                                              "summary": "Auto-captured AI gate learning."})
        return {"status": "created", "page_id": result.get("id"), "title": title}


def capture_from_json_report(json_path: str, evidence_path: str, output_dir: str) -> Path:
    with open(json_path, encoding="utf-8") as fh:
        data = json.load(fh)

    from .gate_engine import Decision
    from .types import CheckResult, Issue, Severity

    checks: list[CheckResult] = []
    for c in data.get("checks", []):
        issues: list[Issue] = []
        for iss in c.get("issues", []):
            sev = Severity(iss["severity"]) if iss.get("severity") in [s.value for s in Severity] else Severity.WARNING
            issues.append(Issue(check_name=c["check_name"], severity=sev, message=iss["message"]))
        checks.append(CheckResult(check_name=c["check_name"], status=c["status"], issues=issues, details=c.get("details", {})))

    decision = Decision(data["decision"])

    report = AcceptanceReport(
        decision=decision,
        task_type=data.get("task_type", "unknown"),
        policy_name=data.get("policy_name", "unknown"),
        repo_path=data.get("repo_path", ""),
        evidence_path=data.get("evidence_path", ""),
        transcript_path=data.get("transcript_path", ""),
        generated_at=data.get("generated_at", ""),
        checks=checks,
        failed_checks=data.get("summary", {}).get("failed_checks", []),
        blocked_checks=data.get("summary", {}).get("blocked_checks", []),
        warning_checks=data.get("summary", {}).get("warning_checks", []),
        required_next_action=data.get("required_next_action", ""),
    )

    return write_capture(report, evidence_path, output_dir)
