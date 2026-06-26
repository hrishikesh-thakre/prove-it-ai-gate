from __future__ import annotations

import re
from pathlib import Path

from .types import CheckResult, Issue, Severity

BARE_COMPLETENESS_PATTERNS = [
    re.compile(r"(?:data\s+)?completeness\s*:\s*complete\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"(?:coverage|audit)\s*:\s*(?:complete|full|100%)\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"scope\s*:\s*all\s*$", re.IGNORECASE | re.MULTILINE),
]

SCOPED_COMPLETENESS_PATTERNS = [
    re.compile(r"(?:complete|full|100%)\s+(?:for|across|over|within)\s+", re.IGNORECASE),
    re.compile(r"(?:complete|full|100%).*?(?:corpus|directory|folder|repository|project|module|package)", re.IGNORECASE),
    re.compile(r"(?:partial|incomplete|sample|subset)\s+(?:for|across|over)", re.IGNORECASE),
]

COMPLETENESS_KEYWORDS = [
    "complete", "completeness", "100%", "fully", "exhaustive",
    "all files", "entire codebase", "full coverage",
    "everything", "no gaps", "comprehensive",
]


def _get_evidence_text(evidence_path: str) -> str:
    evidence_root = Path(evidence_path)
    texts: list[str] = []

    for fname in ["closeout.md", "risks.md", "plan.md", "brief.md", "generated_report.md"]:
        fpath = evidence_root / fname
        if fpath.is_file():
            texts.append(fpath.read_text(encoding="utf-8", errors="replace"))

    artifacts_dir = evidence_root / "artifacts"
    if artifacts_dir.is_dir():
        for fpath in artifacts_dir.glob("*.md"):
            texts.append(fpath.read_text(encoding="utf-8", errors="replace"))

    return "\n".join(texts)


def _find_bare_completeness(text: str) -> list[str]:
    findings: list[str] = []
    for pattern in BARE_COMPLETENESS_PATTERNS:
        for match in pattern.finditer(text):
            line = match.group(0).strip()
            if line not in findings:
                findings.append(line)
    return findings


def _find_scoped_completeness(text: str) -> list[str]:
    findings: list[str] = []
    for pattern in SCOPED_COMPLETENESS_PATTERNS:
        for match in pattern.finditer(text):
            line = match.group(0).strip()
            if line not in findings:
                findings.append(line)
    return findings


def _has_completeness_claim(text: str) -> bool:
    lower = text.lower()
    return any(kw.lower() in lower for kw in COMPLETENESS_KEYWORDS)


def check_scope_completeness(evidence_path: str) -> CheckResult:
    issues: list[Issue] = []
    text = _get_evidence_text(evidence_path)

    if not text.strip():
        return CheckResult(
            check_name="scope_completeness_check",
            status="warning",
            issues=[Issue("scope_completeness_check", Severity.WARNING,
                          "No evidence text found for scope completeness check")],
        )

    bare_claims = _find_bare_completeness(text)
    scoped_claims = _find_scoped_completeness(text)
    has_any_completeness = _has_completeness_claim(text)

    for claim in bare_claims:
        still_bare = True
        for scoped in scoped_claims:
            if claim.lower() in scoped.lower():
                still_bare = False
                break
        if still_bare:
            issues.append(Issue(
                "scope_completeness_check",
                Severity.REJECT,
                f"Bare completeness claim without scope: '{claim}'",
            ))

    if has_any_completeness and not scoped_claims and bare_claims:
        issues.append(Issue(
            "scope_completeness_check",
            Severity.REJECT,
            "Completeness claims found but none include a scoped corpus definition",
        ))

    status = "passed"
    if any(i.severity == Severity.REJECT for i in issues):
        status = "failed"

    return CheckResult(
        check_name="scope_completeness_check",
        status=status,
        issues=issues,
        details={
            "bare_completeness_claims": bare_claims,
            "scoped_completeness_claims": scoped_claims,
            "has_any_completeness_claim": has_any_completeness,
        },
    )
