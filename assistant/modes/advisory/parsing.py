"""Turning free text into advisory fields with plain regular expressions (no model needed).

Where this sits: `modes.advisory`, lowest layer. `extraction.py` uses these parsers as the
offline fallback and for the "answer to the question just asked" case; the opening-message
extractor here is used on its own.

Key ideas:
- Keyword tables are data: adding a usage type or a phrase is a one-line change.
- "Strict" parsing says nothing rather than guess, so the caller can tell whether the
  customer actually answered the question.
- Every numeric value passes one plausibility check (`plausible`), whichever path read it.

Read next: modes/advisory/extraction.py.
"""
from __future__ import annotations

import re
from typing import Any

from core_common.types import CommitmentPreference, UsageType

# --- Keyword tables -----------------------------------------------------------------
_USAGE_KEYWORDS: list[tuple[str, UsageType]] = [
    (r"student|stud(y|ies|ying)|school|universit|college|homework", UsageType.STUDENT),
    (r"famil|\bkids?\b|child", UsageType.FAMILY),
    (r"home.?office|office|remote|work(ing)? from home|\bwfh\b|\bwork", UsageType.HOME_OFFICE),
    (r"\bgam(e|es|ing|er|ers)\b|playstation|xbox|console", UsageType.GAMING),
    (r"stream|netflix|film|movie|series|\bshows?\b|watch", UsageType.STREAMING),
    (r"basic|simple|normal|everyday|browsing|e-?mail", UsageType.BASIC),
]

_COMMITMENT_KEYWORDS: list[tuple[str, CommitmentPreference]] = [
    (
        r"no.?contract|without (a |any )?contract|do(?:n'?t| not) want (a |any )?contract"
        r"|contract.?free|no (minimum term|commitment|tie)|rolling|month.?to.?month|\bnone\b",
        CommitmentPreference.NONE,
    ),
    (r"\b24\b|two.?years?", CommitmentPreference.TWENTY_FOUR),
    (r"\b12\b|one.?year|\ba year\b", CommitmentPreference.TWELVE),
    (
        r"no preference|do(?:n'?t| not) mind|does(?:n'?t| not) matter|not bothered|either|whichever"
        r"|whatever",
        CommitmentPreference.ANY,
    ),
]

SKIP_WORDS_RE = re.compile(
    r"\bskip\b|\bpass\b|don'?t know|not sure|no idea|rather not|prefer not|\bnone\b", re.IGNORECASE
)
_INT_RE = re.compile(r"\d+")
_FLOAT_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def parse_usage(text: str, *, strict: bool = False) -> list[UsageType]:
    """`strict` means "say nothing rather than guess": used when we need to know whether the
    customer actually answered, instead of quietly defaulting them to BASIC."""
    found: list[UsageType] = []
    for pattern, usage in _USAGE_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE) and usage not in found:
            found.append(usage)
    if found:
        return found
    return [] if strict else [UsageType.BASIC]


def parse_int(text: str) -> int | None:
    if SKIP_WORDS_RE.search(text):
        return None
    match = _INT_RE.search(text)
    return int(match.group()) if match else None


def parse_float(text: str) -> float | None:
    if SKIP_WORDS_RE.search(text):
        return None
    match = _FLOAT_RE.search(text)
    if not match:
        return None
    return float(match.group().replace(",", ""))


def parse_commitment(text: str, *, strict: bool = False) -> CommitmentPreference | None:
    for pattern, pref in _COMMITMENT_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return pref
    return None if strict else CommitmentPreference.ANY


# --- Plausibility -------------------------------------------------------------------
# Positional regex parsing misreads real sentences: "50 quid a month" given as the answer
# to "how many devices?" would set device_count=50, and "I can do a 24-month contract"
# would set a £24 budget. A range check is the last guard on every numeric field, whichever
# path (model or regex) produced it.

PLAUSIBLE = {
    "household_size": (1, 30),
    "device_count": (1, 200),
    "budget_gbp": (5.0, 10_000.0),
}

def plausible(field_name: str, value: Any) -> bool:
    bounds = PLAUSIBLE.get(field_name)
    if bounds is None or value is None:
        return value is not None
    low, high = bounds
    return low <= value <= high


# --- The opening message ------------------------------------------------------------
# The message that routed the customer here may already answer several questions ("four of
# us, we stream every evening, budget £50"). There is no "current question" yet, so the
# positional parsers above must not run (a bare "50" would become the device count): only
# clearly labelled patterns are used.

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_NUM = r"(\d+|" + "|".join(_NUMBER_WORDS) + r")"
_OPENING_BUDGET_RES = [
    re.compile(r"£\s*(\d[\d,]*(?:\.\d+)?)"),
    re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:pounds?|quid|gbp)\b", re.IGNORECASE),
    re.compile(r"\bbudget\b\D{0,15}?(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE),
]
_OPENING_HOUSEHOLD_RES = [
    re.compile(rf"\b{_NUM}\s+of\s+us\b", re.IGNORECASE),
    re.compile(rf"\b{_NUM}\s+(?:people|persons|adults|members)\b", re.IGNORECASE),
    re.compile(rf"\b(?:family|household)\s+of\s+{_NUM}\b", re.IGNORECASE),
]
_OPENING_DEVICES_RE = re.compile(rf"\b{_NUM}\s+(?:connected\s+)?(?:devices|gadgets)\b", re.IGNORECASE)
_OPENING_COMMITMENT_RES: list[tuple[re.Pattern[str], CommitmentPreference]] = [
    (
        re.compile(
            r"no.?contract|without (?:a |any )?contract|contract.?free|no (?:minimum term|commitment)"
            r"|month.?to.?month",
            re.IGNORECASE,
        ),
        CommitmentPreference.NONE,
    ),
    (re.compile(r"\b24[- ]months?\b|two[- ]years?\b", re.IGNORECASE), CommitmentPreference.TWENTY_FOUR),
    (
        re.compile(r"\b12[- ]months?\b|one[- ]year\b|\b(?:a|1)[- ]year\b", re.IGNORECASE),
        CommitmentPreference.TWELVE,
    ),
]


def _to_number(raw: str) -> float:
    return float(_NUMBER_WORDS.get(raw.lower(), raw.replace(",", "")))


def extract_opening_with_regex(text: str) -> dict[str, Any]:
    """Only clearly labelled statements; never a positional guess."""
    values: dict[str, Any] = {}
    for pattern in _OPENING_BUDGET_RES:
        match = pattern.search(text)
        if match and plausible("budget_gbp", _to_number(match.group(1))):
            values["budget_gbp"] = _to_number(match.group(1))
            break
    for pattern in _OPENING_HOUSEHOLD_RES:
        match = pattern.search(text)
        if match and plausible("household_size", _to_number(match.group(1))):
            values["household_size"] = int(_to_number(match.group(1)))
            break
    match = _OPENING_DEVICES_RE.search(text)
    if match and plausible("device_count", _to_number(match.group(1))):
        values["device_count"] = int(_to_number(match.group(1)))
    for pattern, preference in _OPENING_COMMITMENT_RES:
        if pattern.search(text):
            values["commitment_preference"] = preference.value
            break
    usage = [u.value for u in parse_usage(text, strict=True) if u is not UsageType.BASIC]
    if usage:
        values["usage"] = usage
    if re.search(r"stati[ck].?ip", text, re.IGNORECASE):
        values["needs_static_ip"] = True
    if re.search(r"\btv\b|television", text, re.IGNORECASE):
        values["needs_tv"] = True
    return values
