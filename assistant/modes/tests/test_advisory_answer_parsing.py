"""Reading a customer's answer.

These cases come from a real conversation with a live model, where positional regex parsing
turned "around 50 quid a month" into a device count of 50 and "I can do a 24 month
contract" into a £24 budget — after which no package could possibly match.
"""
from __future__ import annotations

import pytest
from core_common.types import AdvisoryProfile
from llm.base import ChatMessage
from modes import advisory


class _StubProvider:
    """Returns whatever the test says the sentence states; records what it was asked."""

    name = "stub"
    model = "stub"

    def __init__(self, fields: dict | None = None, fail: bool = False) -> None:
        self._fields = fields or {}
        self._fail = fail
        self.calls: list[str] = []

    def complete(self, *, system, messages, temperature=0.2, max_tokens=None) -> str:
        raise AssertionError("field extraction must use structured output, not free text")

    def structured(self, *, system, messages, schema, temperature=0.0):
        self.calls.append(messages[-1].content)
        if self._fail:
            raise RuntimeError("provider unavailable")
        return schema.model_validate(self._fields)

    def health(self):  # pragma: no cover - not exercised here
        raise NotImplementedError


class _Ctx:
    def __init__(self, provider) -> None:
        self.provider = provider


def test_one_sentence_can_answer_several_questions_at_once() -> None:
    provider = _StubProvider({"household_size": 4, "device_count": 8})
    profile, _ = advisory._apply_answer(
        AdvisoryProfile(), "household_size", "There are 4 of us, with about 8 devices", _Ctx(provider)
    )
    assert profile.household_size == 4
    assert profile.device_count == 8, "the customer should not be asked about devices again"


def test_a_budget_sentence_is_not_read_as_a_device_count() -> None:
    """The regression that broke the live demo."""
    provider = _StubProvider({"budget_gbp": 50.0})
    profile, _ = advisory._apply_answer(
        AdvisoryProfile(), "device_count", "around 50 quid a month", _Ctx(provider)
    )
    assert profile.budget_gbp == 50.0
    assert profile.device_count != 50


def test_a_commitment_sentence_is_not_read_as_a_budget() -> None:
    provider = _StubProvider({"commitment_preference": "24"})
    profile, _ = advisory._apply_answer(
        AdvisoryProfile(), "budget_gbp", "I can do a 24 month contract", _Ctx(provider)
    )
    assert profile.commitment_preference.value == "24"
    assert profile.budget_gbp != 24


@pytest.mark.parametrize(
    "field_name,fields",
    [
        ("budget_gbp", {"budget_gbp": 0.3}),          # nobody buys fibre for 30p
        ("device_count", {"device_count": 5000}),      # not a household
        ("household_size", {"household_size": 900}),
    ],
)
def test_implausible_values_are_ignored_rather_than_stored(field_name, fields) -> None:
    provider = _StubProvider(fields)
    profile, _ = advisory._apply_answer(AdvisoryProfile(), field_name, "...", _Ctx(provider))
    assert getattr(profile, field_name) in (None, 1), "an absurd value must not enter the profile"


def test_regex_fallback_still_parses_the_asked_field_when_the_provider_fails() -> None:
    provider = _StubProvider(fail=True)
    profile, _ = advisory._apply_answer(
        AdvisoryProfile(), "device_count", "8 devices", _Ctx(provider)
    )
    assert profile.device_count == 8
    assert provider.calls, "the provider should have been tried first"


def test_no_provider_means_pure_regex_parsing() -> None:
    profile, _ = advisory._apply_answer(AdvisoryProfile(), "household_size", "there are 4 of us", None)
    assert profile.household_size == 4



def test_a_returning_customer_is_not_asked_everything_again(tenant_config, session_factory) -> None:
    """A follow-up after a recommendation refines it; it does not start from zero.

    Driven through the real Orchestrator across two rounds, because the profile reset that
    caused this lived in its ROUTER -> ADVISORY branch.
    """
    from modes.tests.conftest import build_orchestrator

    orch = build_orchestrator(tenant_config, session_factory)
    first = orch.handle_message(
        conversation_id=None, customer_no="NS-100001", message="I would like a package recommendation"
    )
    conversation_id = first.conversation_id
    for answer in (
        "My family and I stream films and series",
        "There are 4 of us",
        "about 8 devices",
        "£50 a month",
    ):
        orch.handle_message(
            conversation_id=conversation_id, customer_no="NS-100001", message=answer
        )

    # A new advisory message in the same conversation must not wipe what was gathered.
    again = orch.handle_message(
        conversation_id=conversation_id,
        customer_no="NS-100001",
        message="I would like to revisit the package recommendation",
    )
    assert "what do you mainly use the internet for" not in again.reply_en.lower(), (
        "the household profile was discarded and the first question asked again"
    )
