"""Table-driven state machine (contracts §4.3): ``ROUTER -> {ADVISORY, DIAGNOSTIC,
STATUS_QUERY} -> ACTION -> CLOSING``, plus ``AWAITING_APPROVAL`` and ``ESCALATED``.

This module holds only the transition table and limit enforcement — no business logic.
An illegal transition raises ``IllegalTransition`` rather than being silently allowed.

Import as: ``from modes.machine import StateMachine, IllegalTransition, LimitExceeded``.
"""
from __future__ import annotations

from dataclasses import dataclass

from core_common.config import LimitsConfig
from core_common.types import Mode

# The explicit transition table. A conversation may only move from a key's mode to one of
# the modes in its value set. Self-transitions (staying in the same mode across turns,
# e.g. ADVISORY asking its next question) are listed explicitly where legal.
ALLOWED_TRANSITIONS: dict[Mode, frozenset[Mode]] = {
    Mode.ROUTER: frozenset(
        {Mode.ROUTER, Mode.ADVISORY, Mode.DIAGNOSTIC, Mode.STATUS_QUERY, Mode.CLOSING}
    ),
    Mode.ADVISORY: frozenset({Mode.ADVISORY, Mode.CLOSING}),
    Mode.DIAGNOSTIC: frozenset({Mode.DIAGNOSTIC, Mode.ACTION, Mode.ESCALATED, Mode.CLOSING}),
    Mode.STATUS_QUERY: frozenset({Mode.CLOSING}),
    Mode.ACTION: frozenset({Mode.AWAITING_APPROVAL, Mode.ESCALATED, Mode.CLOSING}),
    Mode.AWAITING_APPROVAL: frozenset({Mode.ACTION, Mode.ESCALATED, Mode.CLOSING}),
    Mode.ESCALATED: frozenset({Mode.CLOSING}),
    Mode.CLOSING: frozenset({Mode.ROUTER}),  # a new message after CLOSING starts fresh
}


class IllegalTransition(Exception):
    """Raised when the state machine is asked to move between two modes that are not
    connected in ``ALLOWED_TRANSITIONS`` — a bug in the calling mode handler, never a
    condition to paper over."""

    def __init__(self, current: Mode, target: Mode) -> None:
        super().__init__(f"illegal mode transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


class LimitExceeded(Exception):
    """Raised when a conversation-scoped limit from ``policy.yaml: limits`` is hit
    (``max_tool_calls_per_turn``, ``max_questions_advisory``, ``max_actions_per_conversation``)."""

    def __init__(self, limit_name: str, limit_value: int) -> None:
        super().__init__(f"limit '{limit_name}' ({limit_value}) exceeded")
        self.limit_name = limit_name
        self.limit_value = limit_value


@dataclass
class StateMachine:
    """Stateless transition checker + limit enforcement, parameterised by one tenant's
    ``policy.yaml: limits``."""

    limits: LimitsConfig

    def transition(self, current: Mode, target: Mode) -> Mode:
        """Validate and return ``target``. Raises ``IllegalTransition`` otherwise."""
        allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
        if target not in allowed:
            raise IllegalTransition(current, target)
        return target

    def can_transition(self, current: Mode, target: Mode) -> bool:
        return target in ALLOWED_TRANSITIONS.get(current, frozenset())

    # -- limits -----------------------------------------------------------------------

    def check_tool_calls(self, used: int) -> None:
        if used >= self.limits.max_tool_calls_per_turn:
            raise LimitExceeded("max_tool_calls_per_turn", self.limits.max_tool_calls_per_turn)

    def check_questions_advisory(self, asked: int) -> None:
        if asked >= self.limits.max_questions_advisory:
            raise LimitExceeded("max_questions_advisory", self.limits.max_questions_advisory)

    def check_actions_per_conversation(self, used: int) -> None:
        if used >= self.limits.max_actions_per_conversation:
            raise LimitExceeded(
                "max_actions_per_conversation", self.limits.max_actions_per_conversation
            )
