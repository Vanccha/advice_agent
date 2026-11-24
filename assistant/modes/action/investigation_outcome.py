"""Acting on an agentic investigation's finding: inform the customer or open a ticket.

Where this sits: `modes.action`. `handle_action` calls `handle_investigation_finding` when
the checklist found nothing (`RootCause.NO_ISSUE_FOUND`) and `modes.investigation` produced
a finding.

Key ideas:
- No write action is ever executed here: a finding only shapes the customer reply
  (``resolved_info``) or a structured ticket (``escalate``).
- ``needs_more_info`` never gets here; the dispatcher answers it before ACTION is entered.
- Model text is masked before it reaches the customer, a ticket or the audit log.

Read next: modes/investigation/finding.py, modes/action/escalation.py.
"""
from __future__ import annotations

from typing import Any

from core_common.types import Diagnosis, IssueType, Mode, Priority, StepType
from decision.fallback import PRIORITY_ORDER
from modes import replies
from modes.action.escalation import evidence_record_ids
from modes.action.result import ActionStepResult, department_en
from modes.context import TurnContext
from modes.investigation import FindingOutcome, InvestigationFinding, resolve_department
from modes.ticket_links import record_ticket_link
from privacy.masking import mask_text
from tickets.builder import build_structured_ticket

def scalar_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Model-supplied evidence reduced to ids/statuses: scalar values and flat scalar lists."""
    clean: dict[str, Any] = {}
    for key, value in evidence.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[str(key)] = value
        elif isinstance(value, list) and all(isinstance(v, (str, int, float, bool)) for v in value):
            clean[str(key)] = value
    return clean


def handle_investigation_finding(
    ctx: TurnContext,
    *,
    finding: InvestigationFinding,
    diagnosis: Diagnosis,
    customer_no: str,
    requester_name: str,
    requester_contact: str,
) -> ActionStepResult:
    """Outcome of the agentic investigation (`modes.investigation`) for a ``no_issue_found``
    diagnosis. No write action is ever executed here: the finding only shapes the customer
    reply (``resolved_info``) or a structured ticket (``escalate``); ``needs_more_info`` is
    answered by the orchestrator before ACTION is entered."""
    reply = mask_text(finding.customer_reply_en.strip()).masked
    if finding.outcome is not FindingOutcome.ESCALATE:
        return ActionStepResult(reply_en=reply, next_mode=Mode.CLOSING)

    department, _ = resolve_department(ctx, finding.suggested_department)
    issue_type = IssueType.OTHER
    priority = Priority.NORMAL
    floor = ctx.tenant_config.routing.urgency_floor.get(issue_type.value)
    if floor is not None and PRIORITY_ORDER[Priority(floor).value] > PRIORITY_ORDER[priority.value]:
        priority = Priority(floor)

    record_ids = evidence_record_ids(diagnosis.evidence)
    record_ids.update(scalar_evidence(finding.evidence))
    last_user = next((h["content"] for h in reversed(ctx.history) if h.get("role") == "user"), "")
    summary = finding.summary_en.strip()
    ticket = build_structured_ticket(
        conversation_id=ctx.conversation_id,
        department=department,
        issue_type=issue_type,
        priority=priority,
        subject_en=f"Support request — {customer_no}: {summary[:80]}",
        body_en=(
            f"The standard checks (subscription, payment, provisioning, installation, regional "
            f"incidents, service health) found no known fault for {customer_no}, so the "
            f"assistant investigated further.\n\nInvestigation summary: {summary}\n\n"
            f"Customer's latest message (masked): {last_user[:500]}"
        ),
        requester_customer_no=customer_no,
        requester_name=requester_name,
        requester_contact=requester_contact,
        suggested_next_step_en=(
            f"Review the investigation summary and contact the customer if needed "
            f"(assistant confidence {finding.confidence:.2f})."
        ),
        urgency_reason_en=f"Investigation confidence: {finding.confidence:.2f}",
        evidence_record_ids=record_ids,
        evidence_observations=[summary],
        evidence_queried_sources=list(diagnosis.evidence.get("queried_sources", [])),
        attempted_steps=[
            {"step": "diagnosis:no_issue_found", "result": "standard checklist found no known fault", "outcome": "info"},
            {"step": "agentic_investigation", "result": summary[:300], "outcome": "info"},
        ],
        affected_customers=[customer_no],
    )
    ticket_ref = ctx.ticket_service.create(ticket)
    record_ticket_link(ctx, ticket_key=ticket_ref.ticket_key, department=department.value)
    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.TICKET_CREATED,
        f"created ticket {ticket_ref.ticket_key} for department {department.value} (investigation)",
        mask_text(summary).masked,
        {"ticket_key": ticket_ref.ticket_key, "department": department.value},
        tenant=ctx.tenant,
    )
    dept_en = department_en(ctx, department)
    return ActionStepResult(
        reply_en=replies.investigation_handover(reply, dept_en, ticket_ref.ticket_key),
        next_mode=Mode.ESCALATED,
        ticket_key=ticket_ref.ticket_key,
        actions=[
            {
                "label_en": f"Ticket created: {department.value}",
                "action_name": "investigation_escalation",
                "executed": False,
                "policy_allowed": True,
                "escalated_to": department.value,
                "ticket_key": ticket_ref.ticket_key,
            }
        ],
    )
