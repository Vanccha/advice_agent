"""Posts ticket events to the notification hub's department channels.

Where this sits: called by `services.py`; calls notification-hub over REST.

Key ideas:
- Best-effort side effect: the hub may be down, so every error is swallowed and
  logged and can never fail the ticket request.
- Priority maps to message severity, so urgent tickets look urgent in chat.

Read next: `webhooks.py`.
"""
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
    "TECHNICAL_INFRA": "technical-infra",
    "BILLING": "billing",
    "SUBSCRIPTION_OPS": "subscription-ops",
    "FIELD_INSTALL": "field-install",
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
    """Post a short message to the notification hub.

    Must never fail the caller's request: the hub may be down, so every error is
    swallowed and logged.
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
    """Announce a new ticket in its department's channel."""
    channel_slug = CHANNEL_BY_DEPARTMENT.get(ticket.department)
    if not channel_slug:
        return
    notify_department_channel(
        settings,
        channel_slug=channel_slug,
        title=f"New ticket: {ticket.ticket_key}",
        text=f"{ticket.subject} (priority: {ticket.priority})",
        severity=PRIORITY_TO_SEVERITY.get(ticket.priority, "info"),
        external_ref=ticket.external_ref,
        fields={"ticket_key": ticket.ticket_key, "department": ticket.department},
    )


def notify_ticket_resolved(settings: TicketingSettings, ticket) -> None:
    """Announce a resolved ticket in its department's channel."""
    channel_slug = CHANNEL_BY_DEPARTMENT.get(ticket.department)
    if not channel_slug:
        return
    notify_department_channel(
        settings,
        channel_slug=channel_slug,
        title=f"Ticket resolved: {ticket.ticket_key}",
        text=f"{ticket.subject} has been resolved.",
        severity="info",
        external_ref=ticket.external_ref,
        fields={"ticket_key": ticket.ticket_key, "department": ticket.department},
    )
