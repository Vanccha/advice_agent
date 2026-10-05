"""STATUS_QUERY mode (contracts §4.3): report the current state of an existing ticket or
subscription. Never opens anything new — no tool call here may mutate state.

Import as: ``from modes.status_query import handle_status_query``.
"""
from __future__ import annotations

from dataclasses import dataclass

from core_common.tr import format_money_try
from core_common.types import Mode, StepType
from modes.context import TurnContext
from modes.tool_data import first_record

_SUBSCRIPTION_STATUS_TR = {
    "registered": "kayıt oluşturuldu",
    "awaiting_payment": "ödeme bekleniyor",
    "payment_received": "ödeme alındı, kurulum bekleniyor",
    "provisioning": "kurulum işlemi sürüyor",
    "provisioned": "kurulum tamamlandı, randevu bekleniyor",
    "installation_scheduled": "kurulum randevusu planlandı",
    "active": "aktif",
    "suspended": "askıya alınmış",
    "cancelled": "iptal edilmiş",
}

_TICKET_STATUS_TR = {
    "NEW": "yeni",
    "TRIAGE": "değerlendiriliyor",
    "IN_PROGRESS": "işlemde",
    "WAITING_CUSTOMER": "sizden bilgi bekleniyor",
    "RESOLVED": "çözüldü",
    "CLOSED": "kapatıldı",
    "REJECTED": "reddedildi",
}


@dataclass
class StatusQueryResult:
    reply_tr: str
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
        status_tr = _TICKET_STATUS_TR.get(latest.get("status"), latest.get("status", "bilinmiyor"))
        parts.append(
            f"En güncel talebiniz {latest.get('ticket_key', '—')}: durum '{status_tr}'."
        )
    else:
        parts.append("Açık bir talebiniz görünmüyor.")

    sub_outcome = ctx.call_tool("get_subscription_status", {"customer_no": customer_no})
    sub = first_record(sub_outcome)
    if sub is not None:
        status_tr = _SUBSCRIPTION_STATUS_TR.get(sub.get("status"), sub.get("status", "bilinmiyor"))
        price = sub.get("monthly_price_try")
        price_part = f", aylık {format_money_try(price)}" if price is not None else ""
        parts.append(f"Aboneliğinizin durumu: {status_tr}{price_part}.")

    reply_tr = " ".join(parts)
    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.MODE_DECISION,
        "status_query: reported existing ticket/subscription state",
        "no new ticket or action opened",
        {"ticket_count": len(tickets)},
        tenant=ctx.tenant,
    )
    return StatusQueryResult(reply_tr=reply_tr, next_mode=Mode.CLOSING)
