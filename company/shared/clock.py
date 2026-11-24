"""Time helpers: timezone-aware UTC "now" and a canonical ISO-8601 formatter.

Where this sits: used wherever a service stamps or serialises a time.

Key ideas:
- Store and exchange UTC only; naive datetimes are a classic source of bugs,
  so every stored timestamp comes from `utcnow()`.
- One wire format (`...Z`) for every service, so consumers parse one shape.

Read next: `db.py`.
"""
from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Timezone-aware current time. All stored timestamps use this."""
    return datetime.now(UTC)


def isoformat(value: datetime | None) -> str | None:
    """Format as UTC ISO-8601 ending in `Z`; naive values are assumed to be UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
