"""Resolve a pending approval: the customer pressed Approve or Decline on the approval card.

Where this sits: called by `Orchestrator.resolve_approval` (which the API's approval
endpoint calls). Reads/updates `asst.pending_approvals`, runs the approved action through
`ActionExecutor`, and may open a ticket if policy blocks it after all.

Key ideas:
- Human-in-the-loop: an irreversible action (money, cancellations) only runs after an
  explicit `confirmed=True`, and the executor still re-checks policy at that moment.
- Idempotent by status: a row that is no longer "pending" cannot be resolved twice.
- Fail towards a human: if the approved action is denied, a ticket is opened rather than
  silently dropping the customer's request.

Read next: modes/action/ (where the approval row is created), policy/executor.py.

See: docs/contracts.md §4.6.
"""
from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from core_common.db import session_scope
from core_common.models import PendingApproval
from core_common.types import Department, IssueType, Mode, Priority, StepType
from modes import replies
from modes.dispatch import lookup_requester
from modes.results import TurnResult
from modes.ticket_links import record_ticket_link
from policy.executor import PolicyDenied
from tickets.builder import build_structured_ticket

if TYPE_CHECKING:
    from modes.orchestrator import Orchestrator


def resolve_approval(orch: Orchestrator, *, approval_id: str, granted: bool) -> TurnResult:
    """Apply the customer's decision and return the follow-up message for the chat."""
    try:
        approval_pk = int(approval_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unknown approval_id: {approval_id!r}") from exc

    with session_scope(orch.session_factory) as session:
        row = session.get(PendingApproval, approval_pk)
        if row is None or row.status != "pending":
            raise ValueError(f"unknown or already-resolved approval_id: {approval_id!r}")
        conv_id = row.conversation_id
        action_name = row.action_name
        stored = row.params or {}
        action_params = stored.get("action_params", {})
        policy_context = stored.get("policy_context", {})
        row.status = "granted" if granted else "denied"
        row.resolved_at = dt.datetime.now(dt.timezone.utc)

    orch.audit_log.append(
        conv_id,
        StepType.APPROVAL_GRANTED if granted else StepType.APPROVAL_DENIED,
        f"approval #{approval_id} {'granted' if granted else 'denied'} for '{action_name}'",
        None,
        {"action_name": action_name},
        tenant=orch.tenant,
    )

    state, customer_no = orch.store.load(conv_id)
    ticket_key: str | None = None
    ctx = orch.build_ctx(conv_id, customer_no)

    if not granted:
        reply_en = replies.APPROVAL_DENIED
        new_mode = Mode.CLOSING
    else:
        try:
            ctx.action_executor.execute(action_name, action_params, policy_context, confirmed=True)
        except PolicyDenied as exc:
            name, contact = lookup_requester(ctx, customer_no)
            department = exc.decision.escalate_to or Department.SUBSCRIPTION_OPS
            ticket = build_structured_ticket(
                conversation_id=conv_id,
                department=department,
                issue_type=IssueType.OTHER,
                priority=Priority.NORMAL,
                subject_en=f"Approved action could not be carried out — {action_name}",
                body_en=f"'{action_name}' was approved but blocked by policy: {exc.decision.reason_en}",
                requester_customer_no=customer_no or "unknown",
                requester_name=name,
                requester_contact=contact,
                suggested_next_step_en=exc.decision.reason_en or "Manual review required.",
                urgency_reason_en="An approved action could not be completed.",
                evidence_record_ids={"action_name": action_name},
                evidence_queried_sources=[],
            )
            ticket_ref = ctx.ticket_service.create(ticket)
            record_ticket_link(ctx, ticket_key=ticket_ref.ticket_key, department=ticket_ref.department)
            ticket_key = ticket_ref.ticket_key
            reply_en = replies.approved_action_blocked(ticket_key)
            new_mode = Mode.ESCALATED
        else:
            reply_en = replies.APPROVAL_GRANTED
            new_mode = Mode.CLOSING

    state.mode = new_mode
    orch.store.save(conv_id, state, customer_no)
    orch.store.append_message(conv_id, "assistant", reply_en, new_mode.value)

    return TurnResult(
        conversation_id=conv_id,
        mode=new_mode.value,
        reply_en=reply_en,
        actions=[],
        ticket_key=ticket_key,
        requires_approval=False,
        approval_id=approval_id,
        diagnosis=state.diagnosis.model_dump(mode="json") if state.diagnosis else None,
    )
