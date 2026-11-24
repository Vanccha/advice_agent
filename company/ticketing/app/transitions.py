"""The ticket state machine as an explicit table.

Where this sits: used by `services.change_status` and by the panel to list the
buttons a ticket may offer.

Key ideas:
- State transitions as an explicit table: `ALLOWED_TRANSITIONS` makes the
  legal moves readable at a glance and an illegal one fails with
  `ILLEGAL_TRANSITION` (409) listing what is allowed.
- Terminal states (`CLOSED`, `REJECTED`) have no way out.
- `OPEN_STATUSES` defines "open" once, for gauges and the panel.

Read next: `services.py`.
"""
from __future__ import annotations

from shared.errors import Conflict

# Allowed status transitions, per docs/contracts.md §1.3.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "NEW": {"TRIAGE", "IN_PROGRESS", "REJECTED"},
    "TRIAGE": {"IN_PROGRESS", "WAITING_CUSTOMER", "REJECTED"},
    "IN_PROGRESS": {"WAITING_CUSTOMER", "RESOLVED", "REJECTED"},
    "WAITING_CUSTOMER": {"IN_PROGRESS", "RESOLVED"},
    "RESOLVED": {"CLOSED", "IN_PROGRESS"},
    "CLOSED": set(),
    "REJECTED": set(),
}

ALL_STATUSES = frozenset(ALLOWED_TRANSITIONS.keys())
OPEN_STATUSES = frozenset({"NEW", "TRIAGE", "IN_PROGRESS", "WAITING_CUSTOMER"})


def check_transition(from_status: str, to_status: str) -> None:
    """Raise 409 `ILLEGAL_TRANSITION` if the move is not allowed."""
    allowed = ALLOWED_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise Conflict(
            "ILLEGAL_TRANSITION",
            f"Cannot move ticket from '{from_status}' to '{to_status}'.",
            from_status=from_status,
            to_status=to_status,
            allowed=sorted(allowed),
        )
