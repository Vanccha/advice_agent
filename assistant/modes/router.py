"""ROUTER mode (contracts §4.3): classify the customer's intent, apply the confidence
floor, and map the result to the next mode. Below the floor, the router hands the
conversation over rather than guessing (contracts §4.5/§4.6).

Import as: ``from modes.router import route_intent, RouterResult``.
"""
from __future__ import annotations

from dataclasses import dataclass

from core_common.types import Intent, Mode, StepType
from decision.base import DecisionContext
from decision.fallback import apply_confidence_floor
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

_SMALLTALK_REPLY_TR = (
    "Merhaba! Paket önerisi, arıza teşhisi ya da mevcut talebinizin durumu hakkında "
    "yardımcı olabilirim. Nasıl destek olabilirim?"
)
_OUT_OF_SCOPE_REPLY_TR = (
    "Bu konuda yardımcı olamıyorum çünkü yetkim NetHız internet hizmetleriyle sınırlı. "
    "İnternet paketiniz, aboneliğiniz veya mevcut bir talebinizle ilgili yazabilirsiniz."
)
_HANDOVER_REPLY_TR = (
    "Ne ile ilgili yardım istediğinizden tam olarak emin olamadım, bu yüzden sizi bir "
    "temsilciye yönlendiriyorum."
)
# When the model itself is unreachable the honest answer is not "I did not understand you" —
# the customer wrote perfectly clearly. `LLMStructuredDecisionService` reports a failed
# provider call as a confidence-0 decision whose rationale starts with this marker.
_PROVIDER_FAILURE_MARKER = "decision provider call failed"
_PROVIDER_DOWN_REPLY_TR = (
    "Şu anda yapay zekâ servisime ulaşamıyorum, bu yüzden talebinizi değerlendiremiyorum. "
    "Sizi bir temsilciye yönlendiriyorum; kısa süre içinde tekrar denemeniz de mümkün."
)


@dataclass
class RouterResult:
    intent: Intent
    next_mode: Mode
    confidence: float
    escalated: bool
    reply_tr: str | None  # set only for terminal intents (smalltalk/out_of_scope/handover)


def route_intent(ctx: TurnContext, masked_message: str) -> RouterResult:
    decision_ctx = DecisionContext(
        conversation_id=ctx.conversation_id,
        tenant=ctx.tenant,
        history=ctx.history + [{"role": "user", "content": masked_message}],
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
            reply_tr=(
                _PROVIDER_DOWN_REPLY_TR
                if _PROVIDER_FAILURE_MARKER in (fallback.decision.rationale or "")
                else _HANDOVER_REPLY_TR
            ),
        )

    intent = fallback.decision.value
    next_mode = _INTENT_TO_MODE[intent]
    reply_tr = None
    if intent is Intent.SMALLTALK:
        reply_tr = _SMALLTALK_REPLY_TR
    elif intent is Intent.OUT_OF_SCOPE:
        reply_tr = _OUT_OF_SCOPE_REPLY_TR

    return RouterResult(
        intent=intent,
        next_mode=next_mode,
        confidence=fallback.decision.confidence,
        escalated=fallback.escalated,
        reply_tr=reply_tr,
    )
