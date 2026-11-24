"""ACTION mode: turn a diagnosis (or investigation finding) into a policy-checked action or a ticket.

Where this sits: called by `modes.dispatch` after DIAGNOSTIC. Runs actions only through
`policy.executor.ActionExecutor`, creates tickets through `tickets.builder`/the ticket
service, and stores approval requests in `asst.pending_approvals`.

Key ideas:
- The model never decides its own authority: every mutating call goes through the policy
  engine; a denial becomes a ticket for a human, a confirmation requirement becomes an
  approval card (contracts §4.6).
- The root-cause -> action table is deterministic. Issue type, urgency and department are
  `DecisionService` calls (contracts §4.5) whose tables here are only the fallback when
  the service is unsure, wrong or unavailable.
- Every outcome tells the customer what was found and what happens next, so a handover
  never leaves them guessing.

Public entry points: `handle_action`, `ActionStepResult`.

Read next, in this order: `result.py` (what a step returns), `known_causes.py` (cause ->
action through policy), `triage.py` (issue type, priority, department), `escalation.py`
(the department ticket), `outage.py` (regional outage and the credit offer),
`investigation_outcome.py` (acting on an investigation finding). Then policy/executor.py.

Import as: ``from modes.action import handle_action, ActionStepResult``.
See: docs/contracts.md §4.3, §4.6.
"""
from __future__ import annotations

from core_common.types import Diagnosis, Mode, RootCause
from modes import replies
from modes.action.investigation_outcome import handle_investigation_finding
from modes.action.known_causes import handle_known_cause
from modes.action.outage import handle_regional_outage
from modes.action.result import ActionStepResult
from modes.context import TurnContext
from modes.investigation import InvestigationFinding

__all__ = ["ActionStepResult", "handle_action"]


def handle_action(
    ctx: TurnContext,
    *,
    diagnosis: Diagnosis,
    customer_no: str,
    requester_name: str,
    requester_contact: str,
    finding: InvestigationFinding | None = None,
) -> ActionStepResult:
    """Pick the outcome for a diagnosis: inform, run a policy-checked action or open a ticket.

    The order matters: the two "nothing to act on" causes are answered first, then a
    regional outage (credit offer), then every cause that has a mapped action (any other cause gets a
    plain "no automatic step" reply).
    """
    root_cause = diagnosis.root_cause

    if root_cause == RootCause.NO_ISSUE_FOUND:
        if finding is not None:
            return handle_investigation_finding(
                ctx,
                finding=finding,
                diagnosis=diagnosis,
                customer_no=customer_no,
                requester_name=requester_name,
                requester_contact=requester_contact,
            )
        return ActionStepResult(reply_en=replies.NO_OBVIOUS_PROBLEM, next_mode=Mode.CLOSING)
    if root_cause == RootCause.CUSTOMER_NOT_FOUND:
        return ActionStepResult(reply_en=replies.CUSTOMER_NOT_FOUND, next_mode=Mode.CLOSING)

    if root_cause == RootCause.REGIONAL_OUTAGE and diagnosis.incident_no:
        return handle_regional_outage(
            ctx,
            diagnosis=diagnosis,
            incident_no=diagnosis.incident_no,
            customer_no=customer_no,
            requester_name=requester_name,
            requester_contact=requester_contact,
        )

    return handle_known_cause(
        ctx,
        diagnosis=diagnosis,
        customer_no=customer_no,
        requester_name=requester_name,
        requester_contact=requester_contact,
    )
