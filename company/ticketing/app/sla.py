from __future__ import annotations

from datetime import datetime, timedelta

# SLA hours by priority, per docs/contracts.md §1.3.
SLA_HOURS: dict[str, int] = {
    "URGENT": 2,
    "HIGH": 8,
    "NORMAL": 24,
    "LOW": 72,
}


def sla_due_at(priority: str, created_at: datetime) -> datetime:
    hours = SLA_HOURS.get(priority)
    if hours is None:
        raise ValueError(f"unknown priority: {priority}")
    return created_at + timedelta(hours=hours)
