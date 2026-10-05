from __future__ import annotations

import pytest

from core_common.config import LimitsConfig
from core_common.types import Mode
from modes.machine import IllegalTransition, LimitExceeded, StateMachine


@pytest.fixture()
def machine() -> StateMachine:
    return StateMachine(LimitsConfig(max_tool_calls_per_turn=2, max_questions_advisory=2, max_actions_per_conversation=1))


def test_legal_transitions_succeed(machine: StateMachine) -> None:
    assert machine.transition(Mode.ROUTER, Mode.DIAGNOSTIC) == Mode.DIAGNOSTIC
    assert machine.transition(Mode.DIAGNOSTIC, Mode.ACTION) == Mode.ACTION
    assert machine.transition(Mode.ACTION, Mode.AWAITING_APPROVAL) == Mode.AWAITING_APPROVAL
    assert machine.transition(Mode.AWAITING_APPROVAL, Mode.CLOSING) == Mode.CLOSING
    assert machine.transition(Mode.CLOSING, Mode.ROUTER) == Mode.ROUTER


def test_illegal_transition_raises(machine: StateMachine) -> None:
    with pytest.raises(IllegalTransition):
        machine.transition(Mode.ROUTER, Mode.AWAITING_APPROVAL)
    with pytest.raises(IllegalTransition):
        machine.transition(Mode.STATUS_QUERY, Mode.ACTION)
    with pytest.raises(IllegalTransition):
        machine.transition(Mode.CLOSING, Mode.ACTION)


def test_can_transition_matches_transition(machine: StateMachine) -> None:
    assert machine.can_transition(Mode.ROUTER, Mode.ADVISORY) is True
    assert machine.can_transition(Mode.ROUTER, Mode.ACTION) is False


def test_limits_enforced(machine: StateMachine) -> None:
    machine.check_tool_calls(0)
    with pytest.raises(LimitExceeded):
        machine.check_tool_calls(2)

    machine.check_questions_advisory(1)
    with pytest.raises(LimitExceeded):
        machine.check_questions_advisory(2)

    machine.check_actions_per_conversation(0)
    with pytest.raises(LimitExceeded):
        machine.check_actions_per_conversation(1)
