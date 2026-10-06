"""Regex/validator primitives for UK GDPR masking (contracts §4.7).

Each detector is intentionally narrow: it is better to miss an edge case than to mask (or
fail to mask) the wrong thing. Composition and substitution happen in `masking.py`.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------------------

# UK National Insurance number: two prefix letters, six digits, a suffix letter A-D,
# optionally written in pairs ("QQ 12 34 56 C"). Upper case only, so ordinary words
# followed by numbers are never mistaken for one. Seeded fake values use prefixes HMRC
# never allocates (contracts §6.1), but the detector deliberately does not care — any
# NI-shaped value is masked.
NATIONAL_ID_RE = re.compile(r"\b[A-Z]{2} ?\d{2} ?\d{2} ?\d{2} ?[A-D]\b")

# UK mobile numbers: "+44 7" or "07" + 9 more digits, loosely separated by spaces/dashes.
PHONE_RE = re.compile(r"(?:\+44[\s-]?|\b0)7\d{3}[\s-]?\d{3}[\s-]?\d{3}\b")

EMAIL_RE = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}\b")

# UK IBAN: "GB" + 2 check digits + 4-letter bank code + 14 digits (22 chars total), loosely
# grouped in blocks of 4 for display.
IBAN_RE = re.compile(r"\bGB\d{2} ?[A-Z]{4}(?: ?\d{4}){3} ?\d{2}\b")

# Candidate payment-card-like digit runs (13-19 digits, optionally grouped). Only promoted
# to "this is a card number" after a Luhn check — see `find_card_numbers`.
_CARD_CANDIDATE_RE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")


# --------------------------------------------------------------------------------------
# Luhn check
# --------------------------------------------------------------------------------------


def luhn_is_valid(digits: str) -> bool:
    """True if `digits` (a string of decimal digits) passes the Luhn checksum."""
    if not digits.isdigit() or len(digits) < 2:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


# --------------------------------------------------------------------------------------
# Finders — each returns (start, end, cleaned_value) spans over the given text
# --------------------------------------------------------------------------------------


def find_emails(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group()) for m in EMAIL_RE.finditer(text)]


def find_ibans(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), re.sub(r"\s", "", m.group())) for m in IBAN_RE.finditer(text)]


def find_phones(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), re.sub(r"\D", "", m.group())) for m in PHONE_RE.finditer(text)]


def find_national_ids(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group()) for m in NATIONAL_ID_RE.finditer(text)]


def find_card_numbers(text: str) -> list[tuple[int, int, str]]:
    """Only digit runs of length 13-19 that also pass Luhn — e.g. a 16-digit order number
    that fails Luhn is correctly left alone."""
    results: list[tuple[int, int, str]] = []
    for m in _CARD_CANDIDATE_RE.finditer(text):
        digits = re.sub(r"[ -]", "", m.group())
        if 13 <= len(digits) <= 19 and luhn_is_valid(digits):
            results.append((m.start(), m.end(), digits))
    return results


def find_full_names(text: str, names: list[str]) -> list[tuple[int, int, str]]:
    """Match only names from an explicitly injected list — never a guess at capitalized
    free-text words (contracts §4.7: masking of names must be driven by a list or by
    structured field names, not pattern-guessed)."""
    candidates = sorted({n for n in names if n.strip()}, key=len, reverse=True)
    if not candidates:
        return []
    pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(n) for n in candidates) + r")\b"
    )
    return [(m.start(), m.end(), m.group()) for m in pattern.finditer(text)]
