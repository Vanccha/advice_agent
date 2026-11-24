"""Small user-facing text formatting helpers (British English, GBP).

Where this sits: ``core_common``; pure functions called by modes, recommendation and tickets
whenever a number or date goes into customer-facing text.

Key ideas:
- Format in one place so every reply shows money and dates the same way.
- Dates are formatted by hand (not ``strftime``) so output does not depend on the OS locale.

Read next: ``recommendation/engine.py`` (a typical caller).
"""
from __future__ import annotations

import datetime as dt

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def format_money(amount: float) -> str:
    """``1234.5`` -> ``"£1,234.50"``."""
    sign = "-" if amount < 0 else ""
    return f"{sign}£{abs(amount):,.2f}"


def format_date(value: dt.date) -> str:
    """``date(2026, 10, 5)`` -> ``"5 October 2026"``."""
    return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"


def format_count(count: int, singular: str, plural: str | None = None) -> str:
    """``format_count(3, "device")`` -> ``"3 devices"``; ``1`` keeps the singular."""
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {noun}"
