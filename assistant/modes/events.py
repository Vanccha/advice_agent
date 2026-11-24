"""Inbound events that are not a customer message: ticket status webhooks and monitoring alerts.

Where this sits: called by `Orchestrator.handle_ticket_event` / `handle_alert`, which the
API's webhook endpoints call. Uses `asst.ticket_links` and `asst.alert_events`, the ticket
service, and the gateway (to post to a department channel).

Key ideas:
- The assistant reacts to the company's world (a department changed a ticket, Alertmanager
  fired) without any customer typing: these are the system-initiated entry points.
- Unknown or irrelevant events return `None` rather than raising: a webhook sender must
  never be told to retry something we deliberately ignore.
- Alerts are recorded first, handled second, so nothing is lost if handling fails.

Read next: modes/ticket_links.py, api/main.py (the webhook routes).

See: docs/contracts.md §1.5, §4.8.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from core_common.db import session_scope
from core_common.models import AlertEvent, TicketLink
from core_common.types import Department, IssueType, Mode, Priority, StepType
from modes import replies
from modes.results import TurnResult
from modes.ticket_links import record_ticket_link
from privacy.masking import mask_text
from tickets.builder import build_structured_ticket

if TYPE_CHECKING:
    from modes.orchestrator import Orchestrator

# Only these alerts justify an automatic ticket; anything else is recorded and left alone.
_ACTIONABLE_ALERTS = ("PaymentGatewayDown", "CoreApiDown")


def handle_ticket_event(orch: Orchestrator, event: dict[str, Any]) -> TurnResult | None:
    """Tell the customer their ticket changed status (None if the ticket is not ours)."""
    ticket_key = event.get("ticket_key")
    if not ticket_key:
        return None

    with session_scope(orch.session_factory) as session:
        link = (
            session.query(TicketLink)
            .filter_by(ticket_key=ticket_key)
            .order_by(TicketLink.id.desc())
            .first()
        )
        if link is None:
            return None
        conv_id = link.conversation_id
        new_status = event.get("new_status") or event.get("status")
        link.last_known_status = new_status
        link.notified_status = new_status

    status_en = replies.TICKET_STATUS.get(new_status, new_status or "updated")
    comment = event.get("comment")
    # A department comment is free text: mask it before it reaches the customer chat.
    reply_en = replies.ticket_updated(
        ticket_key, status_en, mask_text(str(comment)).masked if comment else None
    )

    orch.audit_log.append(
        conv_id,
        StepType.USER_NOTIFIED,
        f"notified user of ticket {ticket_key} status change",
        None,
        {"ticket_key": ticket_key, "new_status": new_status},
        tenant=orch.tenant,
    )
    orch.store.append_message(conv_id, "assistant", reply_en, Mode.CLOSING.value)

    return TurnResult(
        conversation_id=conv_id,
        mode=Mode.CLOSING.value,
        reply_en=reply_en,
        actions=[],
        ticket_key=ticket_key,
        requires_approval=False,
        approval_id=None,
        diagnosis=None,
    )


def handle_alert(orch: Orchestrator, alert: dict[str, Any]) -> TurnResult | None:
    """Record a monitoring alert; for infrastructure outages open an urgent ticket and
    notify the department channel. Returns None when no automated action applies."""
    fingerprint = str(alert.get("fingerprint") or alert.get("alertname") or uuid.uuid4().hex)
    alertname = str(alert.get("alertname", "unknown"))
    severity = str(alert.get("severity", "warning"))
    status = str(alert.get("status", "firing"))

    with session_scope(orch.session_factory) as session:
        session.add(
            AlertEvent(
                alert_fingerprint=fingerprint,
                alertname=alertname,
                severity=severity,
                status=status,
                labels=alert.get("labels") or {},
                annotations=alert.get("annotations") or {},
                handled=False,
            )
        )

    conv_id = f"alert-{fingerprint}"
    ctx = orch.build_ctx(conv_id)
    orch.audit_log.append(
        conv_id,
        StepType.ALERT_RECEIVED,
        f"received alert '{alertname}' (status={status})",
        None,
        {"alertname": alertname, "severity": severity},
        tenant=orch.tenant,
    )

    if status != "firing" or alertname not in _ACTIONABLE_ALERTS:
        _mark_alert_handled(orch, fingerprint, "no automated action for this alert")
        return None

    department_code = str(alert.get("department") or Department.TECHNICAL_INFRA.value)
    try:
        department = Department(department_code)
    except ValueError:
        department = Department.TECHNICAL_INFRA

    ticket = build_structured_ticket(
        conversation_id=conv_id,
        department=department,
        issue_type=IssueType.PAYMENT_SYSTEM_DOWN,
        priority=Priority.URGENT,
        subject_en=f"{alertname} — detected automatically by the monitoring system",
        body_en=(
            f"Alert from the monitoring system: {alert.get('summary') or alertname}. "
            f"Status: {status}, severity: {severity}."
        ),
        requester_customer_no="SYSTEM",
        requester_name="Monitoring System",
        requester_contact="n/a",
        suggested_next_step_en="Check the payment/core API infrastructure.",
        urgency_reason_en="Risk of a system-wide outage.",
        evidence_record_ids={"alert_fingerprint": fingerprint},
        evidence_queried_sources=["mcp-monitoring:webhooks/alertmanager"],
        source="monitoring",
    )
    ticket_ref = ctx.ticket_service.create(ticket)
    record_ticket_link(ctx, ticket_key=ticket_ref.ticket_key, department=ticket_ref.department)

    channel = orch.tenant_config.departments.get(department.value)
    if channel is not None:
        ctx.gateway.call_sync(
            "post_department_message",
            {
                "channel": channel.channel,
                "title": alertname,
                "text": f"{alertname} fired, ticket: {ticket_ref.ticket_key}",
                "severity": severity,
                "source": "assistant",
            },
        )

    orch.audit_log.append(
        conv_id,
        StepType.TICKET_CREATED,
        f"created ticket {ticket_ref.ticket_key} from alert '{alertname}'",
        None,
        {"ticket_key": ticket_ref.ticket_key},
        tenant=orch.tenant,
    )
    _mark_alert_handled(orch, fingerprint, f"ticket {ticket_ref.ticket_key} created")

    return TurnResult(
        conversation_id=conv_id,
        mode=Mode.ESCALATED.value,
        reply_en=replies.payment_system_unavailable(ticket_ref.ticket_key),
        actions=[],
        ticket_key=ticket_ref.ticket_key,
        requires_approval=False,
        approval_id=None,
        diagnosis=None,
    )


def _mark_alert_handled(orch: Orchestrator, fingerprint: str, note: str) -> None:
    with session_scope(orch.session_factory) as session:
        row = (
            session.query(AlertEvent)
            .filter_by(alert_fingerprint=fingerprint)
            .order_by(AlertEvent.id.desc())
            .first()
        )
        if row is not None:
            row.handled = True
            row.handling_note = note
