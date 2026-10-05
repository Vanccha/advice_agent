"""Small Turkish user-facing text formatting helpers.

Import as: ``from core_common.tr import format_money_try, format_date_tr``.
"""
from __future__ import annotations

import datetime as dt

_MONTHS_TR = (
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)


def format_money_try(amount: float) -> str:
    """``1234.5`` -> ``"1.234,50 TL"`` (Turkish thousands `.`, decimal `,`)."""
    sign = "-" if amount < 0 else ""
    formatted = f"{abs(amount):,.2f}"  # "1,234.50"
    # Swap separators: "," (thousands) -> placeholder, "." (decimal) -> ",", placeholder -> "."
    formatted = formatted.replace(",", "\u0000").replace(".", ",").replace("\u0000", ".")
    return f"{sign}{formatted} TL"


def format_date_tr(value: dt.date) -> str:
    """``date(2026, 10, 5)`` -> ``"5 Ekim 2026"``."""
    return f"{value.day} {_MONTHS_TR[value.month - 1]} {value.year}"


def format_count_tr(count: int, noun_tr: str) -> str:
    """Turkish does not pluralize nouns after a number: ``"3 cihaz"``, not ``"3 cihazlar"``."""
    return f"{count} {noun_tr}"
