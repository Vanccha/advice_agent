"""STATUS_QUERY mode (contracts §4.3): report the current state of an existing ticket or
subscription. Never opens anything new — no tool call here may mutate state.

Import as: ``from modes.status_query import handle_status_query``.
"""
from __future__ import annotations

from dataclasses import dataclass

from core_common.text import format_money
from core_common.types import Mode, StepType
from modes.context import TurnContext
from modes.tool_data import first_record

_SUBSCRIPTION_STATUS_EN = {
    "registered": "registered",
    "awaiting_payment": "awaiting payment",
    "payment_received": "payment received, waiting for setup",
    "provisioning": "setup in progress",
    "provisioned": "setup complete, waiting for an appointment",
    "installation_scheduled": "installation appointment booked",
    "active": "active",
    "suspended": "suspended",
    "cancelled": "cancelled",
}

_TICKET_STATUS_EN = {
    "NEW": "new",
    "TRIAGE": "under review",
    "IN_PROGRESS": "in progress",
    "WAITING_CUSTOMER": "waiting for information from you",
    "RESOLVED": "resolved",
    "CLOSED": "closed",
    "REJECTED": "rejected",
}


@dataclass
class StatusQueryResult:
    reply_en: str
    next_mode: Mode


def handle_status_query(ctx: TurnContext, customer_no: str) -> StatusQueryResult:
    parts: list[str] = []

    tickets = []
    try:
        tickets = ctx.ticket_service.list_for_customer(customer_no)
    except Exception:
        tickets = []
    ctx.tool_calls_used += 1

    if tickets:
        latest = tickets[0]
        status_en = _TICKET_STATUS_EN.get(latest.get("status"), latest.get("status", "unknown"))
        parts.append(
            f"Your most recent request is {latest.get('ticket_key', '—')}: status '{status_en}'."
        )
    else:
        parts.append("I cannot see any open requests for you.")

    sub_outcome = ctx.call_tool("get_subscription_status", {"customer_no": customer_no})
    sub = first_record(sub_outcome)
    if sub is not None:
        status_en = _SUBSCRIPTION_STATUS_EN.get(sub.get("status"), sub.get("status", "unknown"))
        price = sub.get("monthly_price_gbp")
        price_part = f", {format_money(price)} a month" if price is not None else ""
        parts.append(f"Your subscription status: {status_en}{price_part}.")

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
