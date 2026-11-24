"""ConversationState: round-trip and compatibility with rows stored by older versions."""
from __future__ import annotations

from core_common.types import Diagnosis, DiagnosisScope, Mode
from modes.state import ConversationState


def test_new_conversation_defaults() -> None:
    state = ConversationState.from_stored(None)
    assert state.mode is Mode.ROUTER
    assert state.history == []
    assert state.diagnosis is None
    assert state.investigation_rounds == 0
    assert not state.has_open_investigation()


def test_round_trip_preserves_every_field() -> None:
    state = ConversationState(
        mode=Mode.DIAGNOSTIC,
        customer_no="NS-100001",
        history=[{"role": "user", "content": "hi"}],
        diagnosis=Diagnosis(root_cause="no_issue_found", scope=DiagnosisScope.CUSTOMER_SPECIFIC, confidence=0.4),
        advisory_profile={"budget_gbp": 30.0},
        advisory_awaiting_field="budget_gbp",
        advisory_questions_asked=2,
        investigation_rounds=1,
        investigation_prior={"questions": ["Which light?"], "summary": "s"},
    )
    stored = state.to_stored()
    assert stored["mode"] == "DIAGNOSTIC"  # JSON-safe: plain strings, not enum members
    assert ConversationState.from_stored(stored) == state


def test_legacy_row_with_missing_and_unknown_keys_loads() -> None:
    legacy = {
        "mode": "ADVISORY",
        "history": [{"role": "user", "content": "hello"}],
        "_advisory_first_turn": True,  # old transient key that no longer exists
        "some_future_key": {"a": 1},
    }
    state = ConversationState.from_stored(legacy)
    assert state.mode is Mode.ADVISORY
    assert state.history == [{"role": "user", "content": "hello"}]
    assert state.advisory_profile == {}
    assert state.customer_no is None
    assert "some_future_key" not in state.to_stored()


def test_legacy_nulls_and_empty_diagnosis_become_defaults() -> None:
    state = ConversationState.from_stored(
        {"mode": None, "history": None, "diagnosis": {}, "advisory_profile": None}
    )
    assert state.mode is Mode.ROUTER
    assert state.history == []
    assert state.diagnosis is None
    assert state.advisory_profile == {}


def test_clear_investigation_resets_counters() -> None:
    state = ConversationState(investigation_rounds=2, investigation_prior={"questions": ["q"]})
    assert state.has_open_investigation()
    state.clear_investigation()
    assert not state.has_open_investigation()
    assert state.investigation_prior == {}
