import pytest

from decision.base import DecisionContext
from decision.typesafe_jev import TypeSafeJevDecisionService


def _ctx() -> DecisionContext:
    return DecisionContext(conversation_id="conv-1", tenant="nethiz")


def test_every_method_raises_not_implemented_with_a_documented_contract():
    service = TypeSafeJevDecisionService()
    for method_name in (
        "classify_intent",
        "choose_department",
        "assess_urgency",
        "classify_issue_type",
    ):
        with pytest.raises(NotImplementedError) as excinfo:
            getattr(service, method_name)(_ctx())
        message = str(excinfo.value)
        assert "Decision" in message
        assert "confidence" in message
