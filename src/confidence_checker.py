from __future__ import annotations

import re
from pathlib import Path

from .types import CheckResult, Issue, Severity

CONFIDENCE_PATTERN = re.compile(
    r"(?:confidence|conf|probability)[\s:]*"
    r"(?:is\s+)?(?:above\s+)?(?:approximately\s+)?"
    r"(0?\.\d+|1\.0|100%?)",
    re.IGNORECASE,
)

DEFAULT_HIGH_CONFIDENCE_THRESHOLD = 0.90
DEFAULT_HEURISTIC_CONFIDENCE_CAP = 0.75

COMPLETENESS_PHRASES = [
    "complete", "100%", "fully covered", "no unresolved risks",
    "all files", "everything", "comprehensive",
    "exhaustive", "fully audited", "no gaps",
]


def _extract_confidence_values(text: str) -> list[float]:
    values: list[float] = []
    for match in CONFIDENCE_PATTERN.finditer(text):
        raw = match.group(1).lower()
        if raw in ("100%", "100"):
            values.append(1.0)
        else:
            try:
                v = float(raw)
                if 0 <= v <= 1:
                    values.append(v)
            except ValueError:
                pass
    return values


def _has_completeness_claim(text: str) -> bool:
    lower = text.lower()
    return any(phrase.lower() in lower for phrase in COMPLETENESS_PHRASES)


def _has_deterministic_evidence(evidence_path: str) -> bool:
    artifacts_dir = Path(evidence_path) / "artifacts"
    if not artifacts_dir.is_dir():
        return False
    inventory = artifacts_dir / "inventory.json"
    findings = artifacts_dir / "extracted_findings.json"
    return inventory.is_file() and findings.is_file()


def _check_heuristic_in_transcript(transcript_path: str) -> bool:
    path = Path(transcript_path)
    if not path.is_file():
        return False

    raw = path.read_text(encoding="utf-8", errors="replace").lower()

    import json as _json
    tool_texts: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if event.get("type") in ("tool_result", "tool_call") or event.get("role") == "tool":
            for field in ("stdout", "stderr", "content", "output", "command"):
                val = event.get(field)
                if isinstance(val, str):
                    tool_texts.append(val)

    combined = " ".join(tool_texts).lower()
    heuristic_markers = [
        "grep ", "rg ", "select-string", "findstr",
        "filename match", "keyword match", "keyword filter",
        "regex search", "regex match",
        "heuristic extraction", "heuristic method", "heuristic search",
        "manually selected", "manually grouped",
    ]
    if any(marker in combined for marker in heuristic_markers):
        return True

    if ("heuristic" in combined and
        any(ctx in combined for ctx in ["extraction", "method", "search", "matching", "filter"])):
        return True

    return False


def check_confidence_claims(evidence_path: str, transcript_path: str = "",
                            high_threshold: float = 0.90, heuristic_cap: float = 0.75) -> CheckResult:
    issues: list[Issue] = []
    evidence_root = Path(evidence_path)
    all_text_parts: list[str] = []

    text_files = ["closeout.md", "risks.md", "plan.md", "brief.md"]
    for fname in text_files:
        fpath = evidence_root / fname
        if fpath.is_file():
            t = fpath.read_text(encoding="utf-8", errors="replace")
            all_text_parts.append(t)

    if transcript_path:
        tpath = Path(transcript_path)
        if tpath.is_file():
            all_text_parts.append(tpath.read_text(encoding="utf-8", errors="replace"))

    combined = "\n".join(all_text_parts)
    confidence_values = _extract_confidence_values(combined)
    has_completeness = _has_completeness_claim(combined)
    has_evidence = _has_deterministic_evidence(evidence_path)
    used_heuristic = _check_heuristic_in_transcript(transcript_path)

    max_conf = max(confidence_values) if confidence_values else 0

    if max_conf > high_threshold and not has_evidence:
        issues.append(Issue(
            "confidence_claim_check",
            Severity.BLOCKER if max_conf >= (high_threshold + 0.05) else Severity.REJECT,
            f"Confidence claim of {max_conf:.2f} found but no deterministic evidence artifacts present (threshold: {high_threshold})",
        ))
    elif max_conf > high_threshold and has_evidence:
        issues.append(Issue(
            "confidence_claim_check",
            Severity.WARNING,
            f"Confidence claim of {max_conf:.2f} is high; verify evidence supports this (threshold: {high_threshold})",
        ))

    if used_heuristic and max_conf > heuristic_cap:
        issues.append(Issue(
            "confidence_claim_check",
            Severity.REJECT,
            f"Confidence ({max_conf:.2f}) exceeds heuristic extraction cap ({heuristic_cap})",
        ))

    if has_completeness and not has_evidence:
        issues.append(Issue(
            "confidence_claim_check",
            Severity.BLOCKER,
            "Completeness claim found but no deterministic evidence artifacts present",
        ))
    elif has_completeness and used_heuristic:
        issues.append(Issue(
            "confidence_claim_check",
            Severity.REJECT,
            "Completeness claim found but extraction appears to use heuristic methods",
        ))

    if max_conf == 1.0 and not has_evidence:
        issues.append(Issue(
            "confidence_claim_check",
            Severity.BLOCKER,
            "100% confidence or completeness claim requires scoped deterministic evidence",
        ))

    status = "passed"
    if any(i.severity == Severity.BLOCKER for i in issues):
        status = "blocked"
    elif any(i.severity == Severity.REJECT for i in issues):
        status = "failed"
    elif any(i.severity == Severity.WARNING for i in issues):
        status = "warning"

    return CheckResult(
        check_name="confidence_claim_check",
        status=status,
        issues=issues,
        details={
            "max_confidence_found": max_conf,
            "confidence_values": confidence_values,
            "has_completeness_claim": has_completeness,
            "has_deterministic_evidence": has_evidence,
            "heuristic_methods_detected": used_heuristic,
            "threshold_high": high_threshold,
            "threshold_heuristic_cap": heuristic_cap,
        },
    )
