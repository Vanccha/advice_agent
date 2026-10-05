"""Regex/validator primitives for KVKK masking (contracts §4.7).

Each detector is intentionally narrow: it is better to miss an edge case than to mask (or
fail to mask) the wrong thing. Composition and substitution happen in `masking.py`.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------------------

# 11 digits, used as a Turkish national id. Seeded fake values always have a non-zero
# first digit (contracts §6.1) and are never also a valid phone number (which always
# carries a leading trunk/country marker) — see module docstring reasoning below.
NATIONAL_ID_RE = re.compile(r"\b[1-9]\d{10}\b")

# Turkish mobile numbers: optional "+90"/"0" + one of the reserved operator prefixes
# (contracts §6.2) + 7 more digits, loosely separated by spaces/dashes.
_OPERATOR_PREFIXES = ("530", "532", "535", "541", "544", "551", "555")
PHONE_RE = re.compile(
    r"(?:\+90[\s-]?|0)(?:" + "|".join(_OPERATOR_PREFIXES) + r")[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}\b"
)

EMAIL_RE = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}\b")

# Turkish IBAN: "TR" + 2 check digits + 20 bank/account digits (26 chars total), loosely
# grouped in blocks of 4 for display.
IBAN_RE = re.compile(r"\bTR\d{2}(?:[ ]?\d{4}){5}[ ]?\d{2}\b")

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
