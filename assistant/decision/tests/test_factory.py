import pytest

from core_common.settings import AssistantSettings
from decision.factory import get_decision_service
from decision.llm_structured import LLMStructuredDecisionService
from decision.typesafe_jev import TypeSafeJevDecisionService
from llm.scripted import ScriptedProvider


def test_llm_structured_is_the_default():
    settings = AssistantSettings(DECISION_SERVICE="llm_structured")
    service = get_decision_service(ScriptedProvider(), settings)
    assert isinstance(service, LLMStructuredDecisionService)


def test_typesafe_jev_is_selectable():
    settings = AssistantSettings(DECISION_SERVICE="typesafe_jev")
    service = get_decision_service(ScriptedProvider(), settings)
    assert isinstance(service, TypeSafeJevDecisionService)


def test_unknown_decision_service_raises():
    settings = AssistantSettings(DECISION_SERVICE="not_a_real_service")
    with pytest.raises(ValueError):
        get_decision_service(ScriptedProvider(), settings)
