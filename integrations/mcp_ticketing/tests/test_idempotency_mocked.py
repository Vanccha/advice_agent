"""`create_structured_ticket` must surface the ticketing service's own
idempotency behaviour faithfully: a fresh `external_ref` comes back `201`
(`already_existed=False`), a repeat comes back `200` with the same ticket
(`already_existed=True`). Mocked so this never depends on, or mutates, the
shared live ticketing service.
"""
from __future__ import annotations

import httpx
import respx

from app.deps import settings
from app.models import CreateStructuredTicketInput
from app.tools import handle_create_structured_ticket

_TICKET_BODY = {
    "id": 1,
    "ticket_key": "TKT-2026-00001",
    "department": "BILLING",
    "status": "NEW",
    "priority": "HIGH",
    "issue_type": "double_charge",
    "subject": "Cift tahsilat",
    "body": "test body",
    "requester_customer_no": "NH-100042",
    "requester_name": "Test User",
    "requester_contact": "+905551234567",
    "source": "api",
    "external_ref": "conv-test:double_charge",
    "incident_ref": None,
    "evidence": None,
    "attempted_steps": None,
    "suggested_next_step": None,
    "affected_customers": None,
    "urgency_reason": None,
    "assignee": None,
    "sla_due_at": "2026-10-06T06:24:20Z",
    "created_at": "2026-10-05T22:24:20Z",
    "updated_at": "2026-10-05T22:24:20Z",
    "resolved_at": None,
    "comments": [],
    "status_history": [],
}


def _make_input() -> CreateStructuredTicketInput:
    return CreateStructuredTicketInput(
        department="BILLING",
        issue_type="double_charge",
        priority="HIGH",
        subject="Cift tahsilat",
        body="test body",
        source="api",
        external_ref="conv-test:double_charge",
    )


async def test_fresh_external_ref_is_not_already_existed() -> None:
    base_url = settings().TICKETING_API_BASE_URL
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.post("/api/v1/tickets").mock(return_value=httpx.Response(201, json=_TICKET_BODY))
        result = await handle_create_structured_ticket(_make_input())

    assert result.ok is True
    assert result.data.already_existed is False
    assert result.data.ticket.ticket_key == "TKT-2026-00001"


async def test_repeat_external_ref_is_already_existed() -> None:
    base_url = settings().TICKETING_API_BASE_URL
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.post("/api/v1/tickets").mock(return_value=httpx.Response(200, json=_TICKET_BODY))
        result = await handle_create_structured_ticket(_make_input())

    assert result.ok is True
    assert result.data.already_existed is True
    assert result.data.ticket.ticket_key == "TKT-2026-00001"


async def test_create_structured_ticket_never_raises_when_ticketing_is_down() -> None:
    base_url = settings().TICKETING_API_BASE_URL
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.post("/api/v1/tickets").mock(side_effect=httpx.ConnectError("refused"))
        result = await handle_create_structured_ticket(_make_input())

    assert result.ok is False
    assert result.error.code == "UPSTREAM_UNAVAILABLE"
