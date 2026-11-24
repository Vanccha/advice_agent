"""STATUS_QUERY mode: report the current state of the customer's latest ticket and subscription.

Where this sits: entered from the router; called by `modes.dispatch`. Reads tickets via
the ticket service and the subscription via the gateway; always ends in CLOSING.

Key ideas:
- Strictly read-only: this mode never opens a ticket or runs an action.
- Degrade, don't fail: a missing source simply leaves its sentence out of the reply.

Read next: modes/replies.py (the status wording), modes/tool_data.py.

Import as: ``from modes.status_query import handle_status_query``.
See: docs/contracts.md §4.3.
"""
from __future__ import annotations

from dataclasses import dataclass

from core_common.text import format_money
from core_common.types import Mode, StepType
from modes import replies
from modes.context import TurnContext
from modes.tool_data import first_record


@dataclass
class StatusQueryResult:
    """The reply and the mode to move to (always CLOSING: a status report ends the flow)."""

    reply_en: str
    next_mode: Mode


def handle_status_query(ctx: TurnContext, customer_no: str) -> StatusQueryResult:
    """Report the latest ticket and the subscription status, read-only."""
    parts: list[str] = []

    tickets = []
    try:
        tickets = ctx.ticket_service.list_for_customer(customer_no)
    except Exception:
        # A ticketing outage must not block the subscription half of the answer.
        tickets = []
    ctx.tool_calls_used += 1

    if tickets:
        latest = tickets[0]
        status_en = replies.TICKET_STATUS.get(latest.get("status"), latest.get("status", "unknown"))
        parts.append(replies.latest_request_status(latest.get("ticket_key", "—"), status_en))
    else:
        parts.append(replies.NO_OPEN_REQUESTS)

    sub_outcome = ctx.call_tool("get_subscription_status", {"customer_no": customer_no})
    sub = first_record(sub_outcome)
    if sub is not None:
        status_en = replies.SUBSCRIPTION_STATUS.get(sub.get("status"), sub.get("status", "unknown"))
        price = sub.get("monthly_price_gbp")
        price_part = f", {format_money(price)} a month" if price is not None else ""
        parts.append(replies.subscription_status_line(status_en, price_part))

    reply_en = " ".join(parts)
    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.MODE_DECISION,
        "status_query: reported existing ticket/subscription state",
        "no new ticket or action opened",
        {"ticket_count": len(tickets)},
        tenant=ctx.tenant,
    )
    return StatusQueryResult(reply_en=reply_en, next_mode=Mode.CLOSING)
