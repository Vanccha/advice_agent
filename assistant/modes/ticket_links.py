"""Persist the link between a conversation and the ticket it opened.

Where this sits: called by `modes.action`, `modes.incident_policy`, `modes.approvals` and
`modes.events` right after a ticket is created. Writes `asst.ticket_links`.

Key ideas:
- The ticketing system knows nothing about conversations; this table is the assistant's own
  memory of "ticket X belongs to conversation Y", which is what makes status notifications
  possible.

Read next: modes/events.py (the reader of this table).

Import as: ``from modes.ticket_links import record_ticket_link``.
See: docs/contracts.md §1.5.
"""
from __future__ import annotations

from core_common.db import session_scope
from core_common.models import TicketLink
from modes.context import TurnContext


def record_ticket_link(ctx: TurnContext, *, ticket_key: str, department: str) -> None:
    """Remember which conversation opened which ticket, so a later status webhook can
    find the customer to notify."""
    with session_scope(ctx.audit_log.session_factory) as session:
        session.add(
            TicketLink(conversation_id=ctx.conversation_id, ticket_key=ticket_key, department=department)
        )
