"""Reading a customer's answer.

These cases come from a real conversation with a live model, where positional regex parsing
turned "Aylık 500 lira civarı olsun" into a device count of 500 and "24 ay taahhüt
verebilirim" into a 24 TL budget — after which no package could possibly match.
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
    profile = advisory._apply_answer(
        AdvisoryProfile(), "household_size", "Evde 4 kişiyiz, 8 civarı cihaz var", _Ctx(provider)
    )
    assert profile.household_size == 4
    assert profile.device_count == 8, "the customer should not be asked about devices again"


def test_a_budget_sentence_is_not_read_as_a_device_count() -> None:
    """The regression that broke the live demo."""
    provider = _StubProvider({"budget_try": 500.0})
    profile = advisory._apply_answer(
        AdvisoryProfile(), "device_count", "Aylık 500 lira civarı olsun", _Ctx(provider)
    )
    assert profile.budget_try == 500.0
    assert profile.device_count != 500


def test_a_commitment_sentence_is_not_read_as_a_budget() -> None:
    provider = _StubProvider({"commitment_preference": "24"})
    profile = advisory._apply_answer(
        AdvisoryProfile(), "budget_try", "24 ay taahhüt verebilirim", _Ctx(provider)
    )
    assert profile.commitment_preference.value == "24"
    assert profile.budget_try != 24


@pytest.mark.parametrize(
    "field_name,fields",
    [
        ("budget_try", {"budget_try": 3.0}),          # nobody buys fibre for 3 TL
        ("device_count", {"device_count": 5000}),      # not a household
        ("household_size", {"household_size": 900}),
    ],
)
def test_implausible_values_are_ignored_rather_than_stored(field_name, fields) -> None:
    provider = _StubProvider(fields)
    profile = advisory._apply_answer(AdvisoryProfile(), field_name, "...", _Ctx(provider))
    assert getattr(profile, field_name) in (None, 1), "an absurd value must not enter the profile"


def test_regex_fallback_still_parses_the_asked_field_when_the_provider_fails() -> None:
    provider = _StubProvider(fail=True)
    profile = advisory._apply_answer(
        AdvisoryProfile(), "device_count", "8 cihaz var", _Ctx(provider)
    )
    assert profile.device_count == 8
    assert provider.calls, "the provider should have been tried first"


def test_no_provider_means_pure_regex_parsing() -> None:
    profile = advisory._apply_answer(AdvisoryProfile(), "household_size", "4 kişiyiz", None)
    assert profile.household_size == 4



def test_a_returning_customer_is_not_asked_everything_again(tenant_config, session_factory) -> None:
    """A follow-up after a recommendation refines it; it does not start from zero.

    Driven through the real Orchestrator across two rounds, because the profile reset that
    caused this lived in its ROUTER -> ADVISORY branch.
    """
    from modes.tests.conftest import build_orchestrator

    orch = build_orchestrator(tenant_config, session_factory)
    first = orch.handle_message(
        conversation_id=None, customer_no="NH-100001", message="Paket önerisi istiyorum"
    )
    conversation_id = first.conversation_id
    for answer in (
        "Ailemle dizi film izliyoruz",
        "Evde 4 kişiyiz",
        "yaklaşık 8 cihaz var",
        "aylık 500 lira",
    ):
        orch.handle_message(
            conversation_id=conversation_id, customer_no="NH-100001", message=answer
        )

    # A new advisory message in the same conversation must not wipe what was gathered.
    again = orch.handle_message(
        conversation_id=conversation_id,
        customer_no="NH-100001",
        message="Paket önerisini tekrar değerlendirmek istiyorum",
    )
    assert "ne için kullanıyorsunuz" not in again.reply_tr.lower(), (
        "the household profile was discarded and the first question asked again"
    )
