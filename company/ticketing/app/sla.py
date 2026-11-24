"""SLA timers: how long a department has to resolve a ticket, by priority.

Where this sits: called when a ticket is created to stamp `sla_due_at`.

Key ideas:
- An SLA is data (a table of hours per priority), not logic scattered across
  the code; monitoring and the panel read the resulting deadline.

Read next: `services.py`.
"""
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
    """Deadline for a ticket of this priority created at `created_at`."""
    hours = SLA_HOURS.get(priority)
    if hours is None:
        raise ValueError(f"unknown priority: {priority}")
    return created_at + timedelta(hours=hours)
