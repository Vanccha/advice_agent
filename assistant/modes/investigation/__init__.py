"""Agentic investigation: a bounded, read-only model loop for problems the checklist cannot name.

Where this sits: DIAGNOSTIC mode, second stage. `modes.dispatch` calls `plan_turn` when
`modes.diagnostic` ends in ``no_issue_found``; a resulting finding is handed to
`modes.action`. Calls the LLM provider and, through the gateway, read-only tools only.

Key ideas:
- Least privilege: the model may call only the intersection of the gateway catalogue and
  ``routing.yaml: investigation_tools``; anything else is refused and the refusal audited.
- Server-side scoping: ``customer_no`` arguments are forced to the signed-in customer, so
  the model cannot look at somebody else's records.
- The model never executes anything: it ends by calling the local ``submit_finding`` tool,
  validated into an `InvestigationFinding` that only shapes a reply or a ticket. Authority
  stays with `policy.engine`; no write action is reachable from here by construction.
- Bounded and fail-safe: step caps, a clarification-round cap, and every failure ends in
  a human handover, never a guess.

Public entry points: `plan_turn`, `InvestigationTurn`, `InvestigationFinding`,
`FindingOutcome`, `resolve_department`, `SUBMIT_FINDING`.

Read next, in this order: `tools.py` (allowlist, scoping), `prompts.py` (what the model is
told), `loop.py` (the bounded loop), `finding.py` (the conclusion and its policy checks);
then `plan_turn` below, which decides what the customer sees.

Import as: ``from modes.investigation import plan_turn, InvestigationFinding``.
See: docs/contracts.md §4.3.
"""
from __future__ import annotations

from dataclasses import dataclass

from core_common.types import Diagnosis
from modes import replies
from modes.context import TurnContext
from modes.investigation.finding import (
    SUBMIT_FINDING,
    FindingOutcome,
    InvestigationFinding,
    compose_question_reply,
    finalize_finding,
    resolve_department,
)
from modes.investigation.loop import InvestigationResult, run_investigation
from modes.state import ConversationState
from privacy.masking import mask_text

__all__ = [
    "SUBMIT_FINDING",
    "FindingOutcome",
    "InvestigationFinding",
    "InvestigationResult",
    "InvestigationTurn",
    "compose_question_reply",
    "finalize_finding",
    "plan_turn",
    "resolve_department",
    "run_investigation",
]


@dataclass
class InvestigationTurn:
    """What `plan_turn` tells the orchestrator: either a reply that keeps the conversation
    in DIAGNOSTIC (`reply_en`), or a finding to hand to `modes.action.handle_action`."""

    reply_en: str | None = None
    finding: InvestigationFinding | None = None



# --- Turn planning ------------------------------------------------------------------
def _fallback_finding(ctx: TurnContext, reason: str | None) -> InvestigationFinding:
    last_user = next(
        (h["content"] for h in reversed(ctx.history) if h.get("role") == "user"), ""
    )
    return InvestigationFinding(
        outcome=FindingOutcome.ESCALATE,
        summary_en=(
            "Standard checks found no known fault and the automated investigation could not "
            f"conclude ({reason or 'unavailable'}). Customer's latest message (masked): "
            f"{mask_text(last_user).masked[:400]}"
        ),
        customer_reply_en=replies.INVESTIGATION_FALLBACK,
        suggested_department=None,
        confidence=0.0,
    )


def plan_turn(
    ctx: TurnContext, state: ConversationState, *, customer_no: str, diagnosis: Diagnosis
) -> InvestigationTurn:
    """Run the investigation for a ``no_issue_found`` diagnosis and decide what happens
    next. Owns the per-conversation clarification counter in `state`."""
    max_rounds = ctx.tenant_config.policy.limits.max_clarification_rounds
    rounds = state.investigation_rounds
    allow_clarification = rounds < max_rounds

    prior = dict(state.investigation_prior)
    result = run_investigation(
        ctx,
        customer_no=customer_no,
        diagnosis=diagnosis,
        allow_clarification=allow_clarification,
        prior=prior,
    )
    finding = result.finding

    if finding is None:
        if allow_clarification:
            state.investigation_rounds = rounds + 1
            return InvestigationTurn(reply_en=replies.INVESTIGATION_ASK_MORE_DETAIL)
        finding = _fallback_finding(ctx, result.fallback_reason)
        finding = finalize_finding(ctx, finding, allow_clarification=False)

    if finding.outcome is FindingOutcome.NEEDS_MORE_INFO:
        state.investigation_rounds = rounds + 1
        question = (finding.clarifying_question_en or "").strip()
        reply = compose_question_reply(finding.customer_reply_en, question)
        state.investigation_prior = {
            "questions": [*prior.get("questions", []), mask_text(question).masked],
            "summary": mask_text(finding.summary_en).masked[:500],
        }
        return InvestigationTurn(reply_en=mask_text(reply).masked)

    state.clear_investigation()
    return InvestigationTurn(finding=finding)
