"""Live checks against the real ticketing service. Skips cleanly when the
stack isn't reachable from the current environment. Every ticket planted
here uses a unique, distinctive `external_ref` / `incident_ref` so it never
collides with seeded or other tests' data.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from app.deps import settings
from app.models import (
    AddTicketCommentInput,
    AttemptedStepInput,
    CreateStructuredTicketInput,
    EvidenceInput,
    FindTicketsByIncidentInput,
    GetTicketInput,
    ListCustomerTicketsInput,
    RequesterInput,
)
from app.tools import (
    handle_add_ticket_comment,
    handle_create_structured_ticket,
    handle_find_tickets_by_incident,
    handle_get_ticket,
    handle_list_customer_tickets,
)


def _ticketing_reachable() -> bool:
    try:
        httpx.get(f"{settings().TICKETING_API_BASE_URL}/health", timeout=2.0)
        return True
    except httpx.HTTPError:
        return False


@pytest.fixture(autouse=True)
def _skip_if_unreachable() -> None:
    if not _ticketing_reachable():
        pytest.skip("ticketing service not reachable from this environment")


def _full_section_4_1_input(external_ref: str, incident_ref: str | None = None) -> CreateStructuredTicketInput:
    return CreateStructuredTicketInput(
        department="BILLING",
        issue_type="double_charge",
        priority="HIGH",
        subject="Cift tahsilat - NS-100042 (2 x £45.90)",
        body="Two payments on the same day, same amount, 4 minutes apart, both succeeded.",
        source="api",
        external_ref=external_ref,
        incident_ref=incident_ref,
        requester=RequesterInput(customer_no="NS-100042", name="A K", contact="+447700900167"),
        evidence=EvidenceInput(
            record_ids={"subscription_id": 42, "payment_ids": [88, 89]},
            error_codes=[],
            observations=["Two payments on the same day, same amount, 4 minutes apart, both succeeded."],
            queried_sources=["diag.payment_status", "payment_api:list_customer_charges"],
        ),
        attempted_steps=[
            AttemptedStepInput(step="get_payment_status", result="2 succeeded charges found", outcome="info"),
            AttemptedStepInput(
                step="policy_check:issue_refund", result="denied: refund_not_permitted", outcome="blocked"
            ),
        ],
        affected_customers=["NS-100042"],
        suggested_next_step="Approve the refund of payment 88 (£45.90).",
        urgency_reason="The customer was charged twice; a refund is due within the statutory period.",
    )


async def test_structured_ticket_round_trips_field_for_field_and_reads_back() -> None:
    external_ref = f"adapter-check-{uuid.uuid4()}"
    inp = _full_section_4_1_input(external_ref)

    created = await handle_create_structured_ticket(inp)
    assert created.ok, created.error
    assert created.source == "ticketing_api"
    assert created.data.already_existed is False
    ticket = created.data.ticket

    assert ticket.department == inp.department
    assert ticket.issue_type == inp.issue_type
    assert ticket.priority == inp.priority
    assert ticket.subject == inp.subject
    assert ticket.body == inp.body
    assert ticket.source == inp.source
    assert ticket.external_ref == external_ref
    assert ticket.requester_customer_no == "NS-100042"
    assert ticket.requester_name == "A K"
    assert ticket.requester_contact == "+447700900167"
    assert ticket.evidence == {
        "record_ids": {"subscription_id": 42, "payment_ids": [88, 89]},
        "error_codes": [],
        "observations": ["Two payments on the same day, same amount, 4 minutes apart, both succeeded."],
        "queried_sources": ["diag.payment_status", "payment_api:list_customer_charges"],
    }
    assert ticket.attempted_steps == [
        {"step": "get_payment_status", "result": "2 succeeded charges found", "outcome": "info"},
        {"step": "policy_check:issue_refund", "result": "denied: refund_not_permitted", "outcome": "blocked"},
    ]
    assert ticket.affected_customers == ["NS-100042"]
    assert ticket.suggested_next_step == "Approve the refund of payment 88 (£45.90)."
    assert ticket.urgency_reason == "The customer was charged twice; a refund is due within the statutory period."

    read_back = await handle_get_ticket(GetTicketInput(ticket_key=ticket.ticket_key))
    assert read_back.ok, read_back.error
    assert read_back.data.ticket.ticket_key == ticket.ticket_key
    assert read_back.data.ticket.subject == inp.subject
    assert read_back.data.ticket.evidence == ticket.evidence
    assert read_back.data.ticket.comments == []
    assert len(read_back.data.ticket.status_history) >= 1


async def test_posting_the_same_external_ref_twice_yields_one_ticket() -> None:
    external_ref = f"adapter-check-dup-{uuid.uuid4()}"
    first = await handle_create_structured_ticket(_full_section_4_1_input(external_ref))
    assert first.ok, first.error
    assert first.data.already_existed is False

    second = await handle_create_structured_ticket(
        CreateStructuredTicketInput(
            department="BILLING",
            issue_type="double_charge",
            priority="HIGH",
            subject="a completely different subject that must be ignored",
            body="different body",
            source="api",
            external_ref=external_ref,
        )
    )
    assert second.ok, second.error
    assert second.data.already_existed is True
    assert second.data.ticket.ticket_key == first.data.ticket.ticket_key
    assert second.data.ticket.subject == first.data.ticket.subject  # untouched, proves it's the same ticket


async def test_find_tickets_by_incident_returns_only_that_incidents_tickets() -> None:
    incident_a = f"INC-TEST-{uuid.uuid4().hex[:8]}"
    incident_b = f"INC-TEST-{uuid.uuid4().hex[:8]}"

    created_a = await handle_create_structured_ticket(
        _full_section_4_1_input(f"adapter-check-{uuid.uuid4()}", incident_ref=incident_a)
    )
    assert created_a.ok, created_a.error
    created_b = await handle_create_structured_ticket(
        _full_section_4_1_input(f"adapter-check-{uuid.uuid4()}", incident_ref=incident_b)
    )
    assert created_b.ok, created_b.error

    found = await handle_find_tickets_by_incident(FindTicketsByIncidentInput(incident_no=incident_a))
    assert found.ok, found.error
    keys = {t.ticket_key for t in found.data.tickets}
    assert created_a.data.ticket.ticket_key in keys
    assert created_b.data.ticket.ticket_key not in keys


async def test_list_customer_tickets_and_add_ticket_comment() -> None:
    external_ref = f"adapter-check-{uuid.uuid4()}"
    created = await handle_create_structured_ticket(_full_section_4_1_input(external_ref))
    assert created.ok, created.error
    ticket_key = created.data.ticket.ticket_key

    listed = await handle_list_customer_tickets(ListCustomerTicketsInput(customer_no="NS-100042"))
    assert listed.ok, listed.error
    assert any(t.ticket_key == ticket_key for t in listed.data.tickets)

    commented = await handle_add_ticket_comment(
        AddTicketCommentInput(ticket_key=ticket_key, body="integration test comment", is_internal=True)
    )
    assert commented.ok, commented.error
    assert commented.data.comment.body == "integration test comment"
    assert commented.data.comment.is_internal is True

    read_back = await handle_get_ticket(GetTicketInput(ticket_key=ticket_key))
    assert read_back.ok, read_back.error
    assert any(c.body == "integration test comment" for c in read_back.data.ticket.comments)


async def test_get_ticket_for_unknown_key_is_ticket_not_found() -> None:
    result = await handle_get_ticket(GetTicketInput(ticket_key="TKT-2026-99999"))
    assert result.ok is False
    assert result.error.code == "TICKET_NOT_FOUND"
