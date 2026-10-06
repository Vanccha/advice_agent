"""Default `DecisionService` (contracts §4.5: `DECISION_SERVICE=llm_structured`).

One structured-output call per decision, each with a tight, English system
prompt, returning the enum value plus a calibrated confidence and a rationale. The
returned value is always validated against the target enum — an out-of-enum answer (or a
failed provider call) becomes a confidence-0 `Decision`, never an exception that kills the
turn; the caller (typically via `decision.fallback.apply_confidence_floor`) is responsible
for escalating on low confidence.

Import as: ``from decision.llm_structured import LLMStructuredDecisionService``.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from core_common.types import Decision, Department, Intent, IssueType, Priority
from decision.base import DecisionContext
from llm.base import ChatMessage, LLMProvider


class _RawDecision(BaseModel):
    """What we ask the model to return for every decision call — kept identical in shape
    across the four decisions so the prompts stay simple and comparable."""

    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class _IntentResult(_RawDecision):
    pass


class _DepartmentResult(_RawDecision):
    pass


class _UrgencyResult(_RawDecision):
    pass


class _IssueTypeResult(_RawDecision):
    pass


_INTENT_SYSTEM = (
    "You are an intent classifier for a telecom ISP's customer support assistant. "
    "Read the masked conversation so far and classify the customer's current intent as "
    "exactly one of: advisory, problem_report, status_query, smalltalk, out_of_scope. "
    "'advisory' means the customer wants help choosing or comparing packages/plans. "
    "'problem_report' means something is broken, delayed, or wrong with their service, "
    "billing, or installation. 'status_query' means they are asking about the status of an "
    "existing ticket or subscription, not reporting something new. 'smalltalk' covers "
    "greetings/thanks with no task. 'out_of_scope' covers anything unrelated to this ISP. "
    "Return structured JSON: `value` (one of the five labels, exact spelling), "
    "`confidence` in [0, 1] calibrated to how certain you really are, and a one-sentence "
    "English `rationale`."
)

_DEPARTMENT_SYSTEM = (
    "You are a routing classifier for a telecom ISP's customer support assistant. Given "
    "the masked conversation, diagnosis, and customer facts so far, decide which internal "
    "department should own this case: TECHNICAL_INFRA (network/infrastructure/outages), "
    "BILLING (payments, refunds, double charges), SUBSCRIPTION_OPS (provisioning, plan "
    "changes, cancellations), or FIELD_INSTALL (installation appointments/technicians). "
    "Return structured JSON: `value` (one of the four department codes, exact spelling), "
    "`confidence` in [0, 1], and a one-sentence English `rationale`."
)

_URGENCY_SYSTEM = (
    "You are an urgency classifier for a telecom ISP's customer support assistant. Given "
    "the masked conversation and diagnosis so far, decide the ticket priority: LOW, "
    "NORMAL, HIGH, or URGENT. URGENT is for active money-loss or total service outage "
    "affecting the customer right now; HIGH for clear, unresolved harm (e.g. a confirmed "
    "double charge, a regional outage); NORMAL for a standard unresolved issue; LOW for "
    "minor/non-blocking requests. Return structured JSON: `value` (one of LOW, NORMAL, "
    "HIGH, URGENT, exact spelling), `confidence` in [0, 1], and a one-sentence English "
    "`rationale`."
)

_ISSUE_TYPE_SYSTEM = (
    "You are an issue-type classifier for a telecom ISP's customer support assistant. "
    "Given the masked conversation and diagnosis so far, classify the underlying issue as "
    "exactly one of: stuck_provisioning, paid_not_active, regional_outage, double_charge, "
    "missed_installation, payment_system_down, plan_change_request, refund_request, "
    "infrastructure_repair, other. Return structured JSON: `value` (one of those labels, "
    "exact spelling), `confidence` in [0, 1], and a one-sentence English `rationale`."
)


def _build_messages(ctx: DecisionContext) -> list[ChatMessage]:
    messages: list[ChatMessage] = [
        ChatMessage(role=turn.get("role", "user"), content=turn.get("content", ""))
        for turn in ctx.history
        if turn.get("content")
    ]

    context_bits: list[str] = []
    if ctx.masked_customer_facts:
        context_bits.append(f"Known customer facts (masked): {ctx.masked_customer_facts}")
    if ctx.diagnosis is not None:
        context_bits.append(f"Diagnosis so far: {ctx.diagnosis.model_dump(mode='json')}")
    if ctx.advisory_profile is not None:
        context_bits.append(f"Advisory profile so far: {ctx.advisory_profile.model_dump(mode='json')}")
    if context_bits:
        messages.append(ChatMessage(role="user", content="\n".join(context_bits)))

    if not messages:
        messages.append(ChatMessage(role="user", content="(empty conversation)"))
    if messages[-1].role != "user":
        messages.append(ChatMessage(role="user", content="(classify based on the conversation above)"))
    return messages


class LLMStructuredDecisionService:
    """Wraps any `LLMProvider` (already PII-guarded by `llm.factory.get_provider`)."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def _decide(
        self,
        ctx: DecisionContext,
        *,
        system_prompt: str,
        result_schema: type[_RawDecision],
        enum_cls: type,
        invalid_fallback: Any,
    ) -> Decision:
        messages = _build_messages(ctx)
        try:
            result = self._provider.structured(
                system=system_prompt, messages=messages, schema=result_schema, temperature=0.0
            )
        except Exception as exc:  # the turn must survive a provider failure
            return Decision(
                value=invalid_fallback,
                confidence=0.0,
                rationale=f"decision provider call failed: {exc}",
                model=getattr(self._provider, "model", "unknown"),
                raw={},
            )

        try:
            value = enum_cls(result.value)
        except ValueError:
            return Decision(
                value=invalid_fallback,
                confidence=0.0,
                rationale=(
                    f"model returned out-of-enum value {result.value!r}; treating as "
                    f"unknown. Original rationale: {result.rationale}"
                ),
                model=getattr(self._provider, "model", "unknown"),
                raw=result.model_dump(mode="json"),
            )

        confidence = max(0.0, min(1.0, result.confidence))
        return Decision(
            value=value,
            confidence=confidence,
            rationale=result.rationale,
            model=getattr(self._provider, "model", "unknown"),
            raw=result.model_dump(mode="json"),
        )

    def classify_intent(self, ctx: DecisionContext) -> Decision[Intent]:
        return self._decide(
            ctx,
            system_prompt=_INTENT_SYSTEM,
            result_schema=_IntentResult,
            enum_cls=Intent,
            invalid_fallback=Intent.OUT_OF_SCOPE,
        )

    def choose_department(self, ctx: DecisionContext) -> Decision[Department]:
        return self._decide(
            ctx,
            system_prompt=_DEPARTMENT_SYSTEM,
            result_schema=_DepartmentResult,
            enum_cls=Department,
            invalid_fallback=Department.SUBSCRIPTION_OPS,
        )

    def assess_urgency(self, ctx: DecisionContext) -> Decision[Priority]:
        return self._decide(
            ctx,
            system_prompt=_URGENCY_SYSTEM,
            result_schema=_UrgencyResult,
            enum_cls=Priority,
            invalid_fallback=Priority.LOW,
        )

    def classify_issue_type(self, ctx: DecisionContext) -> Decision[IssueType]:
        return self._decide(
            ctx,
            system_prompt=_ISSUE_TYPE_SYSTEM,
            result_schema=_IssueTypeResult,
            enum_cls=IssueType,
            invalid_fallback=IssueType.OTHER,
        )
