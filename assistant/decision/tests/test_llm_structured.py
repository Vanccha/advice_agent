from core_common.types import Department, Intent, IssueType, Priority
from decision.base import DecisionContext
from decision.llm_structured import (
    LLMStructuredDecisionService,
    _DepartmentResult,
    _IntentResult,
    _IssueTypeResult,
    _UrgencyResult,
)
from llm.scripted import ScriptedProvider, ScriptedRule


def _ctx(user_text: str) -> DecisionContext:
    return DecisionContext(
        conversation_id="conv-1",
        tenant="nethiz",
        history=[{"role": "user", "content": user_text}],
    )


def test_classify_intent_returns_a_validated_enum_and_confidence():
    provider = ScriptedProvider(
        rules=[
            ScriptedRule(
                match=r"paket değiştirmek",
                structured={
                    "_IntentResult": {
                        "value": "advisory",
                        "confidence": 0.92,
                        "rationale": "customer asks about package options",
                    }
                },
            )
        ]
    )
    service = LLMStructuredDecisionService(provider)
    decision = service.classify_intent(_ctx("paket değiştirmek istiyorum"))
    assert decision.value == Intent.ADVISORY
    assert decision.confidence == 0.92
    assert decision.model == provider.model


def test_out_of_enum_value_becomes_confidence_zero_not_an_exception():
    provider = ScriptedProvider(
        rules=[
            ScriptedRule(
                match=r"garip",
                structured={
                    "_IntentResult": {
                        "value": "not_a_real_intent",
                        "confidence": 0.99,
                        "rationale": "model hallucinated a label",
                    }
                },
            )
        ]
    )
    service = LLMStructuredDecisionService(provider)
    decision = service.classify_intent(_ctx("garip bir mesaj"))
    assert decision.confidence == 0.0
    assert decision.value == Intent.OUT_OF_SCOPE  # the documented invalid-value fallback
    assert "not_a_real_intent" in decision.rationale


def test_provider_failure_is_absorbed_as_confidence_zero_never_raises():
    provider = ScriptedProvider(rules=[])  # nothing matches -> raises UnscriptedPromptError
    service = LLMStructuredDecisionService(provider)
    decision = service.classify_intent(_ctx("hiçbir fixture ile eşleşmeyen mesaj"))
    assert decision.confidence == 0.0
    assert decision.value == Intent.OUT_OF_SCOPE
    assert "decision provider call failed" in decision.rationale


def test_choose_department_validated():
    provider = ScriptedProvider(
        rules=[
            ScriptedRule(
                match=r"çift ödeme",
                structured={
                    "_DepartmentResult": {
                        "value": "BILLING",
                        "confidence": 0.88,
                        "rationale": "duplicate charge detected",
                    }
                },
            )
        ]
    )
    service = LLMStructuredDecisionService(provider)
    decision = service.choose_department(_ctx("çift ödeme alındı"))
    assert decision.value == Department.BILLING
    assert decision.confidence == 0.88


def test_assess_urgency_validated():
    provider = ScriptedProvider(
        rules=[
            ScriptedRule(
                match=r"bölgesel kesinti",
                structured={
                    "_UrgencyResult": {
                        "value": "HIGH",
                        "confidence": 0.8,
                        "rationale": "regional outage affects many customers",
                    }
                },
            )
        ]
    )
    service = LLMStructuredDecisionService(provider)
    decision = service.assess_urgency(_ctx("bölgesel kesinti var"))
    assert decision.value == Priority.HIGH
    assert decision.confidence == 0.8


def test_classify_issue_type_validated():
    provider = ScriptedProvider(
        rules=[
            ScriptedRule(
                match=r"kurulum randevusu",
                structured={
                    "_IssueTypeResult": {
                        "value": "missed_installation",
                        "confidence": 0.75,
                        "rationale": "appointment was missed",
                    }
                },
            )
        ]
    )
    service = LLMStructuredDecisionService(provider)
    decision = service.classify_issue_type(_ctx("kurulum randevusu kaçırıldı"))
    assert decision.value == IssueType.MISSED_INSTALLATION
    assert decision.confidence == 0.75


def test_confidence_is_clamped_into_zero_one():
    provider = ScriptedProvider(
        rules=[
            ScriptedRule(
                match=r"aşırı güven",
                structured={
                    "_IntentResult": {
                        "value": "smalltalk",
                        "confidence": 1.5,  # pydantic ge/le on the schema would already reject this,
                        "rationale": "overconfident",
                    }
                },
            )
        ]
    )
    service = LLMStructuredDecisionService(provider)
    # The schema itself enforces confidence in [0, 1] (Field(ge=0, le=1)), so an
    # out-of-range value fails schema validation inside ScriptedProvider.structured and
    # surfaces as a provider failure -> confidence 0, never an unhandled exception.
    decision = service.classify_intent(_ctx("aşırı güven mesajı"))
    assert decision.confidence == 0.0
