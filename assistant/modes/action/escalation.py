"""Handing a blocked action to a department: the ticket and the customer's handover reply.

Where this sits: `modes.action`. `known_causes.py` calls `escalate_with_ticket` when policy
denies an action; `investigation_outcome.py` reuses the evidence helpers.

Key ideas:
- The ticket text in this module is read by DEPARTMENT AGENTS, not customers: it is
  plain-language and specific so nobody re-does work the assistant already did. Anything the
  customer reads lives in `modes.replies`.
- A ticket carries everything a human needs: diagnosis, what was attempted and why it was
  blocked, evidence ids and a suggested next step.
- Evidence holds identifiers and statuses only; free text goes in its own field.

Read next: tickets/builder.py, modes/replies.py.
See: docs/contracts.md §4.1 (structured ticket).
"""
from __future__ import annotations

from typing import Any

from core_common.types import Department, Diagnosis, IssueType, Mode, Priority, StepType
from modes import replies
from modes.action.result import ActionStepResult, department_en
from modes.context import TurnContext
from modes.ticket_links import record_ticket_link
from tickets.builder import build_structured_ticket

# --- Department-facing text ---------------------------------------------------------
# Read by department agents, NOT by customers (customer-facing text stays in
# `modes.replies`). A subject like "double_charge — NS-100039", or a next step that merely
# repeats the refusal, makes a human redo the work the assistant already did.
_TICKET_SUBJECT_EN: dict[str, str] = {
    "double_charge": "Double charge — {customer_no}",
    "stuck_provisioning": "Provisioning job stuck — {customer_no}",
    "paid_not_active": "Payment received, subscription not active — {customer_no}",
    "regional_outage": "Regional infrastructure fault — {customer_no}",
    "missed_installation": "Installation appointment missed — {customer_no}",
    "payment_system_down": "Payment system unavailable — {customer_no}",
    "refund_request": "Refund request — {customer_no}",
    "plan_change_request": "Package change request — {customer_no}",
    "infrastructure_repair": "Infrastructure repair needed — {customer_no}",
}

_NEXT_STEP_EN: dict[str, str] = {
    "double_charge": "Approve the refund of the duplicate payment (payment records are in the evidence).",
    "missed_installation": "Contact the customer and book a new installation appointment.",
    "regional_outage": "Update the resolution time on the incident record and keep the customer informed.",
    "paid_not_active": "Activate the subscription manually or check the provisioning process.",
    "stuck_provisioning": "Check the provisioning infrastructure; restarting the job did not help.",
    "payment_system_down": "The payment infrastructure team should take over; get back to the customer.",
    "plan_change_request": "Work out the package change and any early termination fee, and tell the customer.",
    "infrastructure_repair": "Dispatch the field/network team.",
}

_ID_EVIDENCE_KEYS = ("record_ids", "observations", "error_codes", "queried_sources")


# --- Evidence helpers ---------------------------------------------------------------
def evidence_record_ids(evidence: dict[str, Any]) -> dict[str, Any]:
    """Ids only — anything that is not an identifier belongs in its own evidence field."""
    nested = evidence.get("record_ids")
    collected: dict[str, Any] = dict(nested) if isinstance(nested, dict) else {}
    for key, value in evidence.items():
        if key in _ID_EVIDENCE_KEYS:
            continue
        collected[key] = value
    return {
        key: value
        for key, value in collected.items()
        if key not in ("observations", "error_codes")
    }


def evidence_list(evidence: dict[str, Any], key: str) -> list[Any]:
    """Merge a list-valued evidence field whether it sits at the top level or under
    `record_ids`, since diagnostic steps write it in both places."""
    merged: list[Any] = []
    for source in (evidence, evidence.get("record_ids") or {}):
        if isinstance(source, dict):
            for item in source.get(key) or []:
                if item not in merged:
                    merged.append(item)
    return merged

# --- Escalation ---------------------------------------------------------------------
def escalate_with_ticket(
    ctx: TurnContext,
    *,
    department: Department,
    issue_type: IssueType,
    priority: Priority,
    diagnosis: Diagnosis,
    customer_no: str,
    requester_name: str,
    requester_contact: str,
    attempted_action: str,
    blocked_reason_en: str,
) -> ActionStepResult:
    """Create a department ticket for an action policy refused and tell the customer who has it."""
    evidence = diagnosis.evidence
    ticket = build_structured_ticket(
        conversation_id=ctx.conversation_id,
        department=department,
        issue_type=issue_type,
        priority=priority,
        subject_en=_TICKET_SUBJECT_EN.get(
            issue_type.value, f"Support request ({issue_type.value}) — {customer_no}"
        ).format(customer_no=customer_no),
        body_en=(
            f"Diagnosis: {diagnosis.root_cause}. For customer {customer_no}, the "
            f"'{attempted_action}' action was blocked by policy: {blocked_reason_en} "
            "Please review it manually."
        ),
        requester_customer_no=customer_no,
        requester_name=requester_name,
        requester_contact=requester_contact,
        suggested_next_step_en=_NEXT_STEP_EN.get(issue_type.value, blocked_reason_en),
        urgency_reason_en=f"Diagnosis confidence: {diagnosis.confidence:.2f}",
        evidence_record_ids=evidence_record_ids(evidence),
        evidence_observations=evidence_list(evidence, "observations"),
        evidence_error_codes=evidence_list(evidence, "error_codes"),
        evidence_queried_sources=evidence.get("queried_sources", []),
        attempted_steps=[
            {"step": f"diagnosis:{diagnosis.root_cause}", "result": "established", "outcome": "info"},
            {"step": f"policy_check:{attempted_action}", "result": blocked_reason_en, "outcome": "blocked"},
        ],
        affected_customers=[customer_no],
        incident_ref=diagnosis.incident_no,
    )
    ticket_ref = ctx.ticket_service.create(ticket)
    record_ticket_link(ctx, ticket_key=ticket_ref.ticket_key, department=department.value)
    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.TICKET_CREATED,
        f"created ticket {ticket_ref.ticket_key} for department {department.value}",
        blocked_reason_en,
        {"ticket_key": ticket_ref.ticket_key, "department": department.value},
        tenant=ctx.tenant,
    )
    dept_en = department_en(ctx, department)
    reply_en = replies.blocked_by_policy_handover(
        diagnosis.root_cause, dept_en, ticket_ref.ticket_key
    )
    return ActionStepResult(
        reply_en=reply_en,
        next_mode=Mode.ESCALATED,
        ticket_key=ticket_ref.ticket_key,
        actions=[
            {
                "label_en": f"Ticket created: {department.value}",
                "action_name": attempted_action,
                "executed": False,
                "policy_allowed": False,
                "escalated_to": department.value,
                "ticket_key": ticket_ref.ticket_key,
            }
        ],
    )
