from __future__ import annotations

import re
from pathlib import Path

from .types import CheckResult, Issue, Severity

HEURISTIC_PATTERNS = [
    re.compile(r"\b(?:grep|rg|select-string|findstr|find)\s+", re.IGNORECASE),
    re.compile(r"\bfilename\s+(?:match|matching|search)\b", re.IGNORECASE),
    re.compile(r"\bkeyword\s+(?:match|matching|search|filter)\b", re.IGNORECASE),
    re.compile(r"\bregex\s+(?:search|match|pattern)\b", re.IGNORECASE),
    re.compile(r"\bmanually\s+(?:selected|grouped|chosen|picked)\b", re.IGNORECASE),
    re.compile(r"\bheuristic\b", re.IGNORECASE),
]

COMPLETENESS_AFTER_HEURISTIC = [
    re.compile(r"complete", re.IGNORECASE),
    re.compile(r"100%", re.IGNORECASE),
    re.compile(r"full\s+coverage", re.IGNORECASE),
    re.compile(r"comprehensive", re.IGNORECASE),
    re.compile(r"no\s+(?:missing|gaps|unresolved)", re.IGNORECASE),
]

HONEST_PARTIAL_PATTERNS = [
    re.compile(r"(?:partial|incomplete|sample|subset)(?:\s+(?:for|across|over|of|scan))?", re.IGNORECASE),
    re.compile(r"(?:not\s+complete|not\s+comprehensive)", re.IGNORECASE),
    re.compile(r"(?:may\s+miss|possible\s+false\s+negative)", re.IGNORECASE),
    re.compile(r"confidence\s*:\s*0?\.[0-7]\d*", re.IGNORECASE),
]


def _get_transcript_text(transcript_path: str) -> str:
    path = Path(transcript_path)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _get_evidence_text(evidence_path: str) -> str:
    evidence_root = Path(evidence_path)
    texts: list[str] = []

    for fname in ["closeout.md", "risks.md", "plan.md", "brief.md"]:
        fpath = evidence_root / fname
        if fpath.is_file():
            texts.append(fpath.read_text(encoding="utf-8", errors="replace"))

    return "\n".join(texts)


def _find_heuristic_patterns(text: str) -> list[str]:
    findings: list[str] = []
    for pattern in HEURISTIC_PATTERNS:
        for match in pattern.finditer(text):
            line = match.group(0).strip()
            if line not in findings:
                findings.append(line)
    return findings


def _has_completeness_after_heuristic(text: str) -> bool:
    return any(pattern.search(text) for pattern in COMPLETENESS_AFTER_HEURISTIC)


def _has_honest_partial_disclaimer(text: str) -> bool:
    return any(pattern.search(text) for pattern in HONEST_PARTIAL_PATTERNS)


def check_heuristic_extraction(evidence_path: str, transcript_path: str = "") -> CheckResult:
    issues: list[Issue] = []

    transcript_text = _get_transcript_text(transcript_path) if transcript_path else ""
    evidence_text = _get_evidence_text(evidence_path)
    combined = transcript_text + "\n" + evidence_text

    heuristic_matches = _find_heuristic_patterns(combined)
    has_completeness = _has_completeness_after_heuristic(combined)

    if heuristic_matches and has_completeness:
        if _has_honest_partial_disclaimer(combined):
            issues.append(Issue(
                "heuristic_extraction_check",
                Severity.WARNING,
                "Heuristic extraction detected but partial-scope disclaimer found",
            ))
        else:
            issues.append(Issue(
                "heuristic_extraction_check",
                Severity.REJECT,
                "Heuristic extraction methods detected but completeness is claimed",
            ))
    elif heuristic_matches:
        issues.append(Issue(
            "heuristic_extraction_check",
            Severity.WARNING,
            f"Heuristic extraction detected: {', '.join(heuristic_matches[:5])}",
        ))

    status = "passed"
    if any(i.severity == Severity.REJECT for i in issues):
        status = "failed"
    elif any(i.severity == Severity.WARNING for i in issues):
        status = "warning"

    return CheckResult(
        check_name="heuristic_extraction_check",
        status=status,
        issues=issues,
        details={
            "heuristic_patterns_found": heuristic_matches,
            "completeness_claimed_after_heuristic": has_completeness,
        },
    )
