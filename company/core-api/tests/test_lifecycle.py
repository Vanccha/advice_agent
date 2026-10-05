"""Pure unit tests for the subscription lifecycle state machine. No database needed."""
from __future__ import annotations

import pytest

from app.lifecycle import ALLOWED_TRANSITIONS, assert_transition, transition
from shared.errors import Conflict


class _FakeSubscription:
    def __init__(self, status: str) -> None:
        self.id = 1
        self.status = status
        self.updated_at = None
        self.activated_at = None
        self.suspended_at = None


class _FakeSession:
    def __init__(self) -> None:
        self.added = []

    def add(self, obj) -> None:
        self.added.append(obj)


LEGAL_PAIRS = [
    ("registered", "awaiting_payment"),
    ("awaiting_payment", "payment_received"),
    ("payment_received", "provisioning"),
    ("provisioning", "provisioned"),
    ("provisioned", "installation_scheduled"),
    ("installation_scheduled", "active"),
    ("active", "suspended"),
    ("suspended", "active"),
    ("registered", "cancelled"),
    ("awaiting_payment", "cancelled"),
    ("active", "cancelled"),
    ("suspended", "cancelled"),
]

ILLEGAL_PAIRS = [
    ("registered", "active"),
    ("registered", "provisioning"),
    ("awaiting_payment", "provisioned"),
    ("payment_received", "active"),
    ("provisioning", "active"),
    ("active", "registered"),
    ("cancelled", "active"),
    ("cancelled", "registered"),
    ("suspended", "provisioning"),
]


@pytest.mark.parametrize("from_status,to_status", LEGAL_PAIRS)
def test_legal_transitions_allowed(from_status, to_status):
    assert_transition(from_status, to_status)  # must not raise


@pytest.mark.parametrize("from_status,to_status", ILLEGAL_PAIRS)
def test_illegal_transitions_rejected(from_status, to_status):
    with pytest.raises(Conflict) as excinfo:
        assert_transition(from_status, to_status)
    assert excinfo.value.code == "ILLEGAL_TRANSITION"
    assert excinfo.value.status_code == 409


def test_cancelled_is_terminal():
    assert ALLOWED_TRANSITIONS["cancelled"] == set()


def test_transition_records_event_and_updates_status():
    sub = _FakeSubscription("registered")
    session = _FakeSession()
    transition(session, sub, "awaiting_payment", actor="api_client", reason="test")
    assert sub.status == "awaiting_payment"
    assert len(session.added) == 1
    event = session.added[0]
    assert event.from_status == "registered"
    assert event.to_status == "awaiting_payment"
    assert event.actor == "api_client"


def test_transition_sets_activated_at():
    sub = _FakeSubscription("installation_scheduled")
    session = _FakeSession()
    transition(session, sub, "active", actor="system")
    assert sub.status == "active"
    assert sub.activated_at is not None


def test_transition_illegal_raises_and_does_not_mutate():
    sub = _FakeSubscription("registered")
    session = _FakeSession()
    with pytest.raises(Conflict):
        transition(session, sub, "active", actor="system")
    assert sub.status == "registered"
    assert session.added == []
