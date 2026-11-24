"""Combined `triage` decision: one provider call, per-field isolation, failure, fallback helper."""
from __future__ import annotations

from core_common.types import Decision, Department, IssueType, Priority, Triage
from decision.base import DecisionContext, triage_via_individual_calls
from decision.llm_structured import LLMStructuredDecisionService
from llm.scripted import ScriptedProvider, ScriptedRule


def _ctx() -> DecisionContext:
    return DecisionContext(
        conversation_id="c", tenant="netswift", history=[{"role": "user", "content": "charged twice"}]
    )


def _field(value: str, confidence: float = 0.9) -> dict:
    return {"value": value, "confidence": confidence, "rationale": "r"}


class _CountingProvider(ScriptedProvider):
    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self.calls = 0

    def structured(self, **kwargs):
        self.calls += 1
        return super().structured(**kwargs)


def _provider(payload: dict) -> _CountingProvider:
    return _CountingProvider([ScriptedRule(match=r"charged twice", structured={"_TriageResult": payload})])


def test_triage_is_one_provider_call() -> None:
    provider = _provider(
        {"issue_type": _field("double_charge"), "priority": _field("HIGH"), "department": _field("BILLING")}
    )
    triage = LLMStructuredDecisionService(provider).triage(_ctx())
    assert provider.calls == 1
    assert triage.issue_type.value is IssueType.DOUBLE_CHARGE
    assert triage.priority.value is Priority.HIGH
    assert triage.department.value is Department.BILLING
    assert triage.priority.confidence == 0.9


def test_invalid_field_does_not_poison_the_others() -> None:
    provider = _provider(
        {"issue_type": _field("nonsense"), "priority": _field("HIGH"), "department": _field("BILLING", 7.0)}
    )
    triage = LLMStructuredDecisionService(provider).triage(_ctx())
    assert triage.issue_type.confidence == 0.0 and triage.issue_type.value is IssueType.OTHER
    assert "out-of-enum" in triage.issue_type.rationale
    assert triage.priority.confidence == 0.9 and triage.priority.value is Priority.HIGH
    assert triage.department.value is Department.BILLING and triage.department.confidence == 1.0


def test_provider_failure_zeroes_all_three() -> None:
    triage = LLMStructuredDecisionService(ScriptedProvider([])).triage(_ctx())
    for d in (triage.issue_type, triage.priority, triage.department):
        assert d.confidence == 0.0
        assert "decision provider call failed" in d.rationale


def test_triage_via_individual_calls_helper() -> None:
    class Fake:
        def classify_issue_type(self, ctx):
            return Decision(value=IssueType.REFUND_REQUEST, confidence=0.8, rationale="a", model="m")

        def assess_urgency(self, ctx):
            return Decision(value=Priority.LOW, confidence=0.7, rationale="b", model="m")

        def choose_department(self, ctx):
            return Decision(value=Department.BILLING, confidence=0.6, rationale="c", model="m")

    triage = triage_via_individual_calls(Fake(), _ctx())
    assert isinstance(triage, Triage)
    assert triage.issue_type.value is IssueType.REFUND_REQUEST
    assert triage.priority.value is Priority.LOW
    assert triage.department.confidence == 0.6
