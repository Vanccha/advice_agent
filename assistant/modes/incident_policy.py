"""Incident ticket deduplication (contracts §4.6 `incident_handling` /
§5 scenario c). During a known regional incident, never open a second ticket for the
same ``incident_no``: find the existing one first and attach to it.

Import as: ``from modes.incident_policy import ensure_incident_ticket``.
"""
from __future__ import annotations

from core_common.types import Department, IssueType, Priority, StepType
from modes.context import TurnContext
from modes.ticket_links import record_ticket_link
from tickets.builder import build_structured_ticket
from tickets.client import TicketRef


def ensure_incident_ticket(
    ctx: TurnContext,
    *,
    incident_no: str,
    customer_no: str,
    requester_name: str,
    requester_contact: str,
) -> TicketRef:
    """Return the ticket tracking ``incident_no``, creating it only if none exists yet
    (contracts §4.6: ``max_tickets_per_incident: 1``)."""
    existing = ctx.ticket_service.find_for_incident(incident_no)
    if existing:
        first = existing[0]
        ticket_ref = TicketRef(
            ticket_key=first["ticket_key"],
            department=first.get("department", Department.TECHNICAL_INFRA.value),
            status=first.get("status", "NEW"),
            priority=first.get("priority"),
            created=False,
        )
        # Attach this customer to the existing incident ticket instead of opening a new
        # one — a comment, never a second `create_structured_ticket` call.
        ctx.call_tool(
            "add_ticket_comment",
            {
                "ticket_key": ticket_ref.ticket_key,
                "author": "assistant",
                "author_type": "system",
                "body": f"Additional affected customer: {customer_no} (regional fault {incident_no}).",
                "is_internal": True,
            },
        )
        record_ticket_link(ctx, ticket_key=ticket_ref.ticket_key, department=ticket_ref.department)
        ctx.audit_log.append(
            ctx.conversation_id,
            StepType.TICKET_CREATED,
            f"attached to existing incident ticket {ticket_ref.ticket_key}",
            f"incident {incident_no} already has a tracking ticket (max_tickets_per_incident=1)",
            {"incident_no": incident_no, "ticket_key": ticket_ref.ticket_key},
            tenant=ctx.tenant,
        )
        return ticket_ref

    # `conversation_id` doubles as the external_ref key here (incident_no, not this one
    # customer's conversation) so a repeat call for the same incident from a *different*
    # conversation still resolves to the same ticket via the ticketing API's own
    # external_ref idempotency, even if `find_for_incident` above were ever unavailable.
    ticket = build_structured_ticket(
        conversation_id=incident_no,
        department=Department.TECHNICAL_INFRA,
        issue_type=IssueType.REGIONAL_OUTAGE,
        priority=Priority.HIGH,
        subject_en=f"Regional outage — {incident_no}",
        body_en=(
            f"Customer {customer_no} has no service because of regional fault "
            f"{incident_no}. Every affected customer in the region will be linked to this "
            "ticket."
        ),
        requester_customer_no=customer_no,
        requester_name=requester_name,
        requester_contact=requester_contact,
        suggested_next_step_en="Follow the regional fault through to resolution.",
        urgency_reason_en="Several customers in the region are without service.",
        evidence_record_ids={"incident_no": incident_no},
        evidence_queried_sources=["mcp-core:get_active_incidents_for_region"],
        incident_ref=incident_no,
        affected_customers=[customer_no],
    )
    ticket_ref = ctx.ticket_service.create(ticket)
    record_ticket_link(ctx, ticket_key=ticket_ref.ticket_key, department=ticket_ref.department)
    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.TICKET_CREATED,
        f"opened incident ticket {ticket_ref.ticket_key}",
        f"no existing ticket found for incident {incident_no}",
        {"incident_no": incident_no, "ticket_key": ticket_ref.ticket_key},
        tenant=ctx.tenant,
    )
    return ticket_ref
