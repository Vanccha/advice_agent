"""Regional outage: attach the customer to the incident ticket and offer a goodwill credit.

Where this sits: `modes.action`. `handle_action` calls `handle_regional_outage` for
`RootCause.REGIONAL_OUTAGE` when the diagnosis names an incident.

Key ideas:
- Two independent concerns are both handled in the same turn: the customer is attached to
  the incident's ticket, and a credit is *offered*.
- The credit is money, so it is irreversible: policy requires the customer's confirmation
  and an approval card is stored; the credit is only applied after they agree
  (`modes.approvals`).

Read next: modes/incident_policy.py, modes/approvals.py.
See: docs/contracts.md §4.6.
"""
from __future__ import annotations

from core_common.types import Diagnosis, Mode
from modes import replies
from modes.action.known_causes import build_params, build_policy_context, persist_pending_approval
from modes.action.result import ActionStepResult
from modes.context import TurnContext
from modes.incident_policy import ensure_incident_ticket
from policy.executor import ConfirmationRequired, PolicyDenied


def handle_regional_outage(
    ctx: TurnContext,
    *,
    diagnosis: Diagnosis,
    incident_no: str,
    customer_no: str,
    requester_name: str,
    requester_contact: str,
) -> ActionStepResult:
    """Attach to the incident ticket, then offer the (confirmation-required) outage credit."""
    root_cause = diagnosis.root_cause
    incident_ticket = ensure_incident_ticket(
        ctx,
        incident_no=incident_no,
        customer_no=customer_no,
        requester_name=requester_name,
        requester_contact=requester_contact,
    )
    # Offer the (confirmation-required, irreversible) outage credit on top of the
    # incident attachment — two independent concerns, both handled this turn.
    action_name = "apply_outage_credit"
    params = build_params(root_cause, diagnosis, customer_no)
    policy_context = build_policy_context(ctx, diagnosis, action_name=action_name, params=params)
    try:
        ctx.action_executor.execute(action_name, params, policy_context, confirmed=False)
    except ConfirmationRequired as exc:
        approval_id = persist_pending_approval(
            ctx, action_name="apply_outage_credit", params=params, policy_context=policy_context,
            prompt_en=exc.decision.reason_en or replies.DEFAULT_APPROVAL_PROMPT,
        )
        reply_en = replies.outage_credit_offer(
            diagnosis.incident_no, incident_ticket.ticket_key
        )
        return ActionStepResult(
            reply_en=reply_en,
            next_mode=Mode.AWAITING_APPROVAL,
            ticket_key=incident_ticket.ticket_key,
            requires_approval=True,
            approval_id=approval_id,
            actions=[
                {
                    "label_en": "Awaiting approval: apply_outage_credit",
                    "action_name": "apply_outage_credit",
                    "executed": False,
                    "policy_allowed": True,
                    "awaiting_confirmation": True,
                }
            ],
        )
    except PolicyDenied as exc:
        reply_en = replies.outage_credit_denied(
            diagnosis.incident_no, incident_ticket.ticket_key, exc.decision.reason_en
        )
        return ActionStepResult(
            reply_en=reply_en, next_mode=Mode.ESCALATED, ticket_key=incident_ticket.ticket_key,
        )
    else:
        # policy.yaml requires confirmation for this action, so this branch is a safety
        # net for a tenant that configures it differently.
        reply_en = replies.outage_attached_only(diagnosis.incident_no, incident_ticket.ticket_key)
        return ActionStepResult(reply_en=reply_en, next_mode=Mode.CLOSING, ticket_key=incident_ticket.ticket_key)
