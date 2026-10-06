import pytest
from pydantic import BaseModel

from llm.base import ChatMessage
from llm.scripted import ScriptedProvider, ScriptedRule, UnscriptedPromptError


class _Issue(BaseModel):
    issue_type: str
    confidence: float


def _messages(user_text: str) -> list[ChatMessage]:
    return [ChatMessage(role="user", content=user_text)]


def test_complete_is_deterministic_for_a_matching_rule():
    provider = ScriptedProvider(
        rules=[ScriptedRule(match=r"hello", reply="Hello, how can I help you?")]
    )
    reply1 = provider.complete(system="sys", messages=_messages("Hello, I have a problem"))
    reply2 = provider.complete(system="sys", messages=_messages("Hello, I have a problem"))
    assert reply1 == reply2 == "Hello, how can I help you?"


def test_complete_raises_on_unscripted_prompt_naming_it():
    provider = ScriptedProvider(rules=[ScriptedRule(match=r"hello", reply="hi")])
    with pytest.raises(UnscriptedPromptError) as excinfo:
        provider.complete(system="sys", messages=_messages("a sentence that will never match"))
    assert "a sentence that will never match" in str(excinfo.value)


def test_structured_validates_against_the_requested_schema():
    provider = ScriptedProvider(
        rules=[
            ScriptedRule(
                match=r"double payment",
                structured={"_Issue": {"issue_type": "double_charge", "confidence": 0.95}},
            )
        ]
    )
    result = provider.structured(system="sys", messages=_messages("a double payment was made"), schema=_Issue)
    assert isinstance(result, _Issue)
    assert result.issue_type == "double_charge"
    assert result.confidence == 0.95


def test_structured_raises_on_unscripted_prompt():
    provider = ScriptedProvider(rules=[])
    with pytest.raises(UnscriptedPromptError):
        provider.structured(system="sys", messages=_messages("anything"), schema=_Issue)


def test_record_mode_collects_unmatched_prompts_but_still_raises():
    provider = ScriptedProvider(rules=[], record=True)
    for prompt in ("first unmatched", "second unmatched"):
        with pytest.raises(UnscriptedPromptError):
            provider.complete(system="sys", messages=_messages(prompt))
    assert provider.unmatched_prompts == ["first unmatched", "second unmatched"]


def test_exact_match_requires_exact_text():
    provider = ScriptedProvider(rules=[ScriptedRule(match="yes", reply="Done.", exact=True)])
    assert provider.complete(system="sys", messages=_messages("yes")) == "Done."
    with pytest.raises(UnscriptedPromptError):
        provider.complete(system="sys", messages=_messages("yes of course"))


def test_health_is_always_ok_and_never_touches_network():
    provider = ScriptedProvider()
    health = provider.health()
    assert health.ok is True
    assert health.provider == "scripted"
