from __future__ import annotations

import logging

from shared.http_client import ServiceClient
from app.settings import TicketingSettings

logger = logging.getLogger(__name__)

PRIORITY_TO_SEVERITY = {
    "URGENT": "critical",
    "HIGH": "warning",
    "NORMAL": "info",
    "LOW": "info",
}

# Department -> notification-hub channel slug, per docs/contracts.md §1.3 / §2.6.
CHANNEL_BY_DEPARTMENT = {
    "TECHNICAL_INFRA": "teknik-altyapi",
    "BILLING": "faturalama",
    "SUBSCRIPTION_OPS": "abonelik-islemleri",
    "FIELD_INSTALL": "saha-kurulum",
}


def notify_department_channel(
    settings: TicketingSettings,
    *,
    channel_slug: str,
    title: str,
    text: str,
    severity: str = "info",
    external_ref: str | None = None,
    fields: dict | None = None,
) -> None:
    """Post a short Turkish message to the notification hub.

    The notification hub may be down or absent entirely; this must never fail the
    caller's request, so every error is swallowed and logged.
    """
    client = ServiceClient(
        base_url=settings.notification_api_base_url,
        api_key=settings.notify_api_key,
        timeout=3.0,
        upstream_name="notification-hub",
    )
    try:
        client.post(
            f"/api/v1/channels/{channel_slug}/messages",
            json_body={
                "title": title,
                "text": text,
                "severity": severity,
                "source": "ticketing",
                "fields": fields or {},
                "external_ref": external_ref,
            },
        )
    except Exception as exc:  # noqa: BLE001 - deliberately broad, must never raise
        logger.warning("notification-hub unreachable or returned an error: %s", exc)


def notify_ticket_created(settings: TicketingSettings, ticket) -> None:
    channel_slug = CHANNEL_BY_DEPARTMENT.get(ticket.department)
    if not channel_slug:
        return
    notify_department_channel(
        settings,
        channel_slug=channel_slug,
        title=f"Yeni talep: {ticket.ticket_key}",
        text=f"{ticket.subject} (öncelik: {ticket.priority})",
        severity=PRIORITY_TO_SEVERITY.get(ticket.priority, "info"),
        external_ref=ticket.external_ref,
        fields={"ticket_key": ticket.ticket_key, "department": ticket.department},
    )


def notify_ticket_resolved(settings: TicketingSettings, ticket) -> None:
    channel_slug = CHANNEL_BY_DEPARTMENT.get(ticket.department)
    if not channel_slug:
        return
    notify_department_channel(
        settings,
        channel_slug=channel_slug,
        title=f"Talep çözüldü: {ticket.ticket_key}",
        text=f"{ticket.subject} çözümlendi.",
        severity="info",
        external_ref=ticket.external_ref,
        fields={"ticket_key": ticket.ticket_key, "department": ticket.department},
    )
