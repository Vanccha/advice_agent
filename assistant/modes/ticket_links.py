"""Tiny helper to persist `asst.ticket_links` rows (contracts §1.5) so a later
`ticket.status_changed` webhook can find which conversation to notify.

Import as: ``from modes.ticket_links import record_ticket_link``.
"""
from __future__ import annotations

from core_common.db import session_scope
from core_common.models import TicketLink
from modes.context import TurnContext


def record_ticket_link(ctx: TurnContext, *, ticket_key: str, department: str) -> None:
    with session_scope(ctx.audit_log.session_factory) as session:
        session.add(
            TicketLink(conversation_id=ctx.conversation_id, ticket_key=ticket_key, department=department)
        )
