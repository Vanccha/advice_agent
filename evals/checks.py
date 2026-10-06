"""Shared assertion vocabulary for both suites.

Every check records expected-vs-actual rather than a bare boolean, so a report can name
*exactly* which expectation failed instead of just "case failed". Nothing here talks to the
assistant or the company stack — pure, dependency-free helpers.

Import as: ``from evals.checks import Check, CaseResult, contains_any, contains_none``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Interpretation of `must_not_blame_customer` (documented in evals/README.md): a
# customer-blaming reply is one that pins the fault on the customer's own card or device
# instead of on the company's system. This is necessarily a judgement call for a
# string-matching harness — the list below is deliberately a module-level constant (not
# inlined into the assertion) so it is easy to find and extend.
BLAME_PHRASES_EN: tuple[str, ...] = (
    "a problem with your card",
    "an issue with your card",
    "caused by your card",
    "your card is invalid",
    "please check your card",
    "check your card details",
    "a problem with your device",
    "caused by your device",
    "a problem with your modem",
    "caused by your modem",
    "your fault",
    "caused by you",
    "your mistake",
    "you entered it incorrectly",
)


@dataclass
class Check:
    """One expectation key's outcome within a case."""

    name: str
    passed: bool
    expected: Any = None
    actual: Any = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "expected": self.expected,
            "actual": self.actual,
            "detail": self.detail,
        }


@dataclass
class CaseResult:
    """One scenario message / advisory profile's full outcome."""

    suite: str
    case_id: str
    variant: str | None = None
    status: str = "PASS"  # "PASS" | "FAIL" | "ERROR" — ERROR is set explicitly, never inferred
    checks: list[Check] = field(default_factory=list)
    message: str = ""
    conversation_id: str | None = None
    reply_en: str | None = None

    @property
    def full_id(self) -> str:
        return f"{self.case_id}[{self.variant}]" if self.variant else self.case_id

    def add(self, name: str, passed: bool, expected: Any = None, actual: Any = None, detail: str = "") -> None:
        self.checks.append(Check(name=name, passed=passed, expected=expected, actual=actual, detail=detail))

    def finalize(self) -> "CaseResult":
        if self.status != "ERROR":
            self.status = "PASS" if all(c.passed for c in self.checks) else "FAIL"
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "case_id": self.case_id,
            "variant": self.variant,
            "status": self.status,
            "message": self.message,
            "conversation_id": self.conversation_id,
            "reply_en": self.reply_en,
            "checks": [c.to_dict() for c in self.checks],
        }


def contains_any(text: str | None, phrases: list[str] | tuple[str, ...]) -> bool:
    lowered = (text or "").lower()
    return any(p.lower() in lowered for p in phrases)


def contains_none(text: str | None, phrases: list[str] | tuple[str, ...]) -> bool:
    return not contains_any(text, phrases)


def error_case(suite: str, case_id: str, variant: str | None, message: str) -> CaseResult:
    """An honest ERROR case (adapter/chaos unavailable, unexpected shape, ...) — never a
    silent pass and never folded into the green total."""
    return CaseResult(suite=suite, case_id=case_id, variant=variant, status="ERROR", message=message)
