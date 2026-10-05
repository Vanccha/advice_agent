from __future__ import annotations

import pytest

from app.transitions import ALLOWED_TRANSITIONS, check_transition

LEGAL_PAIRS = [
    (from_status, to_status)
    for from_status, targets in ALLOWED_TRANSITIONS.items()
    for to_status in targets
]


@pytest.mark.parametrize("from_status,to_status", LEGAL_PAIRS)
def test_legal_transition_passes(from_status: str, to_status: str) -> None:
    check_transition(from_status, to_status)  # must not raise


@pytest.mark.parametrize(
    "from_status,to_status",
    [
        ("NEW", "RESOLVED"),  # must go through TRIAGE or IN_PROGRESS first
        ("CLOSED", "IN_PROGRESS"),  # terminal state
        ("REJECTED", "NEW"),  # terminal state
        ("WAITING_CUSTOMER", "REJECTED"),  # not in the allowed set
    ],
)
def test_illegal_transition_raises_409(from_status: str, to_status: str) -> None:
    from shared.errors import Conflict

    with pytest.raises(Conflict) as exc_info:
        check_transition(from_status, to_status)
    assert exc_info.value.code == "ILLEGAL_TRANSITION"
    assert exc_info.value.status_code == 409


def test_illegal_transition_via_api_returns_409(client, auth_headers) -> None:
    payload = {
        "department": "BILLING",
        "issue_type": "double_charge",
        "priority": "HIGH",
        "subject": "test",
        "body": "test",
        "source": "api",
    }
    created = client.post("/api/v1/tickets", json=payload, headers=auth_headers)
    assert created.status_code == 201
    ticket_key = created.json()["ticket_key"]

    # NEW -> RESOLVED is illegal; must go through TRIAGE/IN_PROGRESS.
    resp = client.patch(
        f"/api/v1/tickets/{ticket_key}",
        json={"status": "RESOLVED"},
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ILLEGAL_TRANSITION"
