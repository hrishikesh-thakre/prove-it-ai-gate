from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Decision(Enum):
    ACCEPT = "ACCEPT"
    ACCEPT_WITH_CONDITIONS = "ACCEPT_WITH_CONDITIONS"
    REJECT = "REJECT"
    BLOCKED = "BLOCKED"


class Severity(Enum):
    BLOCKER = "blocker"
    REJECT = "reject"
    WARNING = "warning"


@dataclass
class Issue:
    check_name: str
    severity: Severity
    message: str
    evidence: str = ""


@dataclass
class CheckResult:
    check_name: str
    status: str
    issues: list[Issue] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
