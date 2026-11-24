"""The opening message of ADVISORY mode already states facts; they must not be asked again."""
from __future__ import annotations

from core_common.types import Mode
from llm.scripted import ScriptedProvider, ScriptedRule
from modes.advisory import _extract_opening_with_regex
from modes.tests.conftest import build_orchestrator

OPENING = "Which package? There are four of us, we stream every evening, my budget is £50"
_INTENT = {"_IntentResult": {"value": "advisory", "confidence": 0.95, "rationale": "scripted"}}


def _provider(extracted: dict | None) -> ScriptedProvider:
    rules = []
    if extracted is not None:
        rules.append(ScriptedRule(match=r"budget is £50", structured={**_INTENT, "_ExtractedFields": extracted}))
    rules.append(ScriptedRule(match=r"package|recommend", structured=_INTENT))
    return ScriptedProvider(rules)


def test_model_extraction_of_the_opening_goes_straight_to_recommendation(tenant_config, session_factory) -> None:
    provider = _provider({"usage": ["streaming"], "household_size": 4, "budget_gbp": 50.0})
    orch = build_orchestrator(tenant_config, session_factory, provider=provider)
    result = orch.handle_message(conversation_id=None, customer_no="NS-100001", message=OPENING)
    assert result.mode == Mode.CLOSING.value


def test_regex_fallback_without_extraction_completes_from_labelled_facts(tenant_config, session_factory) -> None:
    orch = build_orchestrator(tenant_config, session_factory, provider=_provider(None))
    result = orch.handle_message(conversation_id=None, customer_no="NS-100001", message=OPENING)
    assert result.mode == Mode.CLOSING.value


def test_partial_opening_asks_only_for_what_is_missing(tenant_config, session_factory) -> None:
    orch = build_orchestrator(tenant_config, session_factory, provider=_provider(None))
    result = orch.handle_message(
        conversation_id=None, customer_no="NS-100001",
        message="Can you recommend a package? We have 8 devices and a £40 budget",
    )
    assert result.mode == Mode.ADVISORY.value
    assert "mainly use the internet" in result.reply_en  # usage is the first missing field


def test_opening_regex_never_guesses_positionally() -> None:
    assert _extract_opening_with_regex("recommend a package for 50") == {}
    assert _extract_opening_with_regex("I need a package, 24") == {}
    assert _extract_opening_with_regex("4 people, 8 devices, £35, no contract") == {
        "household_size": 4, "device_count": 8, "budget_gbp": 35.0, "commitment_preference": "none",
    }
