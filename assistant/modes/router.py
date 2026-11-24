"""ROUTER mode: classify the customer's intent and pick the next mode.

Where this sits: first handler of every new conversation (and of any message after a
conversation closed). Called by `modes.dispatch`; calls the `DecisionService` for the
classification and `decision.fallback` for the confidence floor.

Key ideas:
- Fail-safe defaults: below the confidence floor the router hands over to a person rather
  than guessing which flow to start (contracts §4.5/§4.6).
- Terminal intents (small talk, out of scope) are answered here and open no working mode.
- Distinguish "the model is down" from "the customer was unclear": the honest reply differs.

Read next: modes/machine.py, modes/dispatch.py, modes/replies.py.

Import as: ``from modes.router import route_intent, RouterResult``.
See: docs/contracts.md §4.3.
"""
from __future__ import annotations

from dataclasses import dataclass

from core_common.types import Intent, Mode, StepType
from decision.base import DecisionContext
from decision.fallback import apply_confidence_floor
from modes import replies
from modes.context import TurnContext
from observability.tracing import record_decision

# contracts §4.3: ROUTER -> {ADVISORY, DIAGNOSTIC, STATUS_QUERY}; smalltalk/out_of_scope
# never open a mode that does real work.
_INTENT_TO_MODE: dict[Intent, Mode] = {
    Intent.ADVISORY: Mode.ADVISORY,
    Intent.PROBLEM_REPORT: Mode.DIAGNOSTIC,
    Intent.STATUS_QUERY: Mode.STATUS_QUERY,
    Intent.SMALLTALK: Mode.CLOSING,
    Intent.OUT_OF_SCOPE: Mode.CLOSING,
}

# `LLMStructuredDecisionService` reports a failed provider call as a confidence-0 decision
# whose rationale starts with this marker. The router uses it to tell "the model is down"
# apart from "the customer was unclear", because the honest reply differs.
_PROVIDER_FAILURE_MARKER = "decision provider call failed"


@dataclass
class RouterResult:
    """Outcome of classifying one message."""

    intent: Intent
    next_mode: Mode
    confidence: float
    escalated: bool
    reply_en: str | None  # set only for terminal intents (smalltalk/out_of_scope/handover)


def route_intent(ctx: TurnContext, masked_message: str) -> RouterResult:
    """Classify the message, apply the confidence floor and pick the next mode.

    Below the floor we hand over instead of guessing: entering the wrong mode would waste
    the customer's turn and could trigger the wrong flow.
    """
    decision_ctx = DecisionContext(
        conversation_id=ctx.conversation_id,
        tenant=ctx.tenant,
        history=ctx.history,  # already ends with the current masked user message
        masked_customer_facts={},
        tenant_config_slice={"known_intents": [i.value for i in Intent]},
    )
    decision = ctx.decision_service.classify_intent(decision_ctx)
    record_decision(
        "classify_intent",
        value=decision.value.value,
        confidence=decision.confidence,
        rationale=decision.rationale,
        model=decision.model,
    )
    fallback = apply_confidence_floor(
        decision,
        ctx.tenant_config.policy.decision.min_confidence,
        ctx.tenant_config.routing,
    )

    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.DECISION_SERVICE,
        f"classified intent as '{fallback.decision.value.value}'",
        fallback.decision.rationale,
        {"confidence": fallback.decision.confidence, "escalated": fallback.escalated},
        tenant=ctx.tenant,
    )

    # Low confidence and nothing in routing.yaml to substitute for a bare Intent: hand
    # over rather than guess which of the four real modes to enter.
    if fallback.escalated and fallback.decision.confidence < ctx.tenant_config.policy.decision.min_confidence:
        return RouterResult(
            intent=fallback.decision.value,
            next_mode=Mode.CLOSING,
            confidence=fallback.decision.confidence,
            escalated=True,
            reply_en=(
                replies.HANDOVER_PROVIDER_DOWN
                if _PROVIDER_FAILURE_MARKER in (fallback.decision.rationale or "")
                else replies.HANDOVER_UNSURE
            ),
        )

    intent = fallback.decision.value
    next_mode = _INTENT_TO_MODE[intent]
    reply_en = None
    if intent is Intent.SMALLTALK:
        reply_en = replies.SMALLTALK
    elif intent is Intent.OUT_OF_SCOPE:
        reply_en = replies.OUT_OF_SCOPE

    return RouterResult(
        intent=intent,
        next_mode=next_mode,
        confidence=fallback.decision.confidence,
        escalated=fallback.escalated,
        reply_en=reply_en,
    )
