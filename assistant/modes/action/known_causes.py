"""Known root causes: the cause -> action table and running the action through policy.

Where this sits: `modes.action`. `handle_known_cause` runs for any root cause the checklist
named (except a regional outage, see `outage.py`). Actions run only through
`policy.executor.ActionExecutor`; approvals are stored in `asst.pending_approvals`.

Key ideas:
- The action name is always deterministic: no model decides *what to try*, only how the
  case is classified and routed (`triage.py`).
- Three outcomes, never a silent failure: policy allows (action done), policy needs the
  customer's confirmation (approval card, contracts §4.6) or policy denies (ticket for a
  department, `escalation.py`).
- The model never decides its own authority: `policy.yaml` does.

Read next: policy/executor.py, modes/action/triage.py, modes/approvals.py.
See: docs/contracts.md §4.6.
"""
from __future__ import annotations

from typing import Any

from core_common.db import session_scope
from core_common.models import PendingApproval
from core_common.types import Diagnosis, IssueType, Mode, Priority, RootCause, StepType
from modes import replies
from modes.action.escalation import escalate_with_ticket
from modes.action.result import ActionStepResult
from modes.action.triage import (
    compute_triage,
    decide_department,
    decide_issue_type,
    decide_priority,
)
from modes.context import TurnContext
from policy.executor import ConfirmationRequired, PolicyDenied

# --- Cause -> action table ----------------------------------------------------------
# root_cause -> (policy action name, deterministic IssueType, deterministic Priority). The
# IssueType/Priority are the *fallback* values `triage.py` uses when the DecisionService is
# unsure, wrong or unavailable (contracts §4.5).
ROOT_CAUSE_ACTION: dict[str, tuple[str, IssueType, Priority]] = {
    RootCause.STUCK_PROVISIONING: (
        "retry_provisioning_job", IssueType.STUCK_PROVISIONING, Priority.NORMAL,
    ),
    RootCause.PAID_NOT_ACTIVE: (
        "enqueue_provisioning_job", IssueType.PAID_NOT_ACTIVE, Priority.NORMAL,
    ),
    RootCause.DOUBLE_CHARGE: (
        "issue_refund", IssueType.DOUBLE_CHARGE, Priority.HIGH,
    ),
    RootCause.MISSED_INSTALLATION: (
        "reschedule_installation", IssueType.MISSED_INSTALLATION, Priority.NORMAL,
    ),
    RootCause.PAYMENT_SYSTEM_DOWN: (
        "repair_infrastructure", IssueType.PAYMENT_SYSTEM_DOWN, Priority.URGENT,
    ),
}

# --- Policy inputs ------------------------------------------------------------------
def build_policy_context(ctx: TurnContext, diagnosis: Diagnosis) -> dict[str, Any]:
    """The facts `policy.yaml` conditions are evaluated against (job, subscription, payment...)."""
    evidence = diagnosis.evidence
    context: dict[str, Any] = {
        "conversation_id": ctx.conversation_id,
        "tenant": ctx.tenant,
        "job": {
            "status": evidence.get("job_status"),
            "attempt_count": evidence.get("job_attempt_count"),
        },
        "subscription": {"status": evidence.get("subscription_status")},
        "payment": {"status": evidence.get("payment_status")},
        "incident": {"exists": diagnosis.incident_no is not None},
        "credit": {"existing_count_30d": 0},
        "amount_gbp": 5.0,
    }
    return context


def build_params(root_cause: str, diagnosis: Diagnosis, customer_no: str) -> dict[str, Any]:
    """The arguments for the mapped action, taken from the diagnosis evidence."""
    evidence = diagnosis.evidence
    base = {"customer_no": customer_no, "subscription_id": evidence.get("subscription_id")}
    if root_cause == RootCause.STUCK_PROVISIONING:
        base["job_id"] = evidence.get("job_id")
    if root_cause == RootCause.REGIONAL_OUTAGE:
        base["amount_gbp"] = 5.0
        base["reason"] = f"Regional outage compensation ({diagnosis.incident_no})"
    return base


# --- Running the action -------------------------------------------------------------
def handle_known_cause(
    ctx: TurnContext,
    *,
    diagnosis: Diagnosis,
    customer_no: str,
    requester_name: str,
    requester_contact: str,
) -> ActionStepResult:
    """Triage the case, ask policy to run the mapped action and turn the verdict into a reply."""
    root_cause = diagnosis.root_cause
    mapping = ROOT_CAUSE_ACTION.get(root_cause)
    if mapping is None:
        return ActionStepResult(
            reply_en=replies.NO_AUTOMATIC_STEP,
            next_mode=Mode.CLOSING,
        )

    action_name, deterministic_issue_type, deterministic_priority = mapping
    triage = compute_triage(ctx, diagnosis)
    issue_type = decide_issue_type(ctx, diagnosis, deterministic_issue_type, triage)
    priority = decide_priority(ctx, diagnosis, issue_type, deterministic_priority, triage)
    policy_context = build_policy_context(ctx, diagnosis)
    params = build_params(root_cause, diagnosis, customer_no)

    try:
        ctx.action_executor.execute(action_name, params, policy_context, confirmed=False)
    except PolicyDenied as exc:
        # policy's own `escalate_to` is authoritative when set (contracts §4.6: the model
        # never decides its own authority) — `choose_department` only fills the gap for an
        # action policy.yaml leaves without one, with its own confidence floor/fallback.
        department = exc.decision.escalate_to or decide_department(ctx, diagnosis, issue_type, triage)
        return escalate_with_ticket(
            ctx,
            department=department,
            issue_type=issue_type,
            priority=priority,
            diagnosis=diagnosis,
            customer_no=customer_no,
            requester_name=requester_name,
            requester_contact=requester_contact,
            attempted_action=action_name,
            blocked_reason_en=exc.decision.reason_en or "blocked by policy",
        )
    except ConfirmationRequired as exc:
        approval_id = persist_pending_approval(
            ctx, action_name=action_name, params=params, policy_context=policy_context,
            prompt_en=exc.decision.reason_en or replies.DEFAULT_APPROVAL_PROMPT,
        )
        return ActionStepResult(
            reply_en=replies.approval_question(exc.decision.reason_en),
            next_mode=Mode.AWAITING_APPROVAL,
            requires_approval=True,
            approval_id=approval_id,
            actions=[
                {
                    "label_en": f"Awaiting approval: {action_name}",
                    "action_name": action_name,
                    "executed": False,
                    "policy_allowed": True,
                    "awaiting_confirmation": True,
                }
            ],
        )
    else:
        return ActionStepResult(
            reply_en=replies.action_success(action_name),
            next_mode=Mode.CLOSING,
            actions=[
                {
                    "label_en": f"Action applied: {action_name}",
                    "action_name": action_name,
                    "executed": True,
                    "policy_allowed": True,
                }
            ],
        )


# --- Approval requests --------------------------------------------------------------
def persist_pending_approval(
    ctx: TurnContext,
    *,
    action_name: str,
    params: dict[str, Any],
    policy_context: dict[str, Any],
    prompt_en: str,
) -> str:
    """Store an approval request for an action that needs the customer's confirmation; returns its id.

    The row keeps the parameters and policy context so the action can be replayed exactly
    as it was requested once the customer agrees (`modes.approvals`).
    """
    with session_scope(ctx.audit_log.session_factory) as session:
        row = PendingApproval(
            conversation_id=ctx.conversation_id,
            action_name=action_name,
            params={"action_params": params, "policy_context": policy_context},
            prompt_en=prompt_en,
            status="pending",
        )
        session.add(row)
        session.flush()
        approval_id = str(row.id)
    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.APPROVAL_REQUESTED,
        f"approval #{approval_id} requested for action '{action_name}'",
        prompt_en,
        {"action_name": action_name},
        tenant=ctx.tenant,
    )
    return approval_id
