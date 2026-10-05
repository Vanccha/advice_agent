import pytest

from core_common.types import Department, IssueType, Priority
from mcp.fake import FakeGateway
from tickets.builder import build_structured_ticket
from tickets.client import TicketRef, TicketService, TicketServiceError


def _ticket():
    return build_structured_ticket(
        conversation_id="conv-1",
        department=Department.BILLING,
        issue_type=IssueType.DOUBLE_CHARGE,
        priority=Priority.HIGH,
        subject_tr="Çift tahsilat",
        body_tr="Detaylar...",
        requester_customer_no="NH-100042",
        requester_name="Ali Kaya",
        requester_contact="+90 532 111 22 31",
        suggested_next_step_tr="İade onaylanmalı.",
        urgency_reason_tr="Yasal süre var.",
    )


def test_create_returns_a_ticket_ref_against_a_fake_gateway():
    gateway = FakeGateway(
        {
            "create_structured_ticket": {
                "ticket_key": "TKT-2026-00014",
                "department": "BILLING",
                "status": "NEW",
                "priority": "HIGH",
                "created": True,
            }
        }
    )
    service = TicketService(gateway)
    ref = service.create(_ticket())
    assert isinstance(ref, TicketRef)
    assert ref.ticket_key == "TKT-2026-00014"
    assert ref.created is True
    # the gateway received the masked payload, not raw PII
    tool_name, arguments = gateway.calls_made[0]
    assert tool_name == "create_structured_ticket"
    assert arguments["requester"]["name"] == "A** K***"


def test_create_raises_a_clear_error_when_the_adapter_is_unavailable():
    gateway = FakeGateway({})  # no canned response -> ADAPTER_UNAVAILABLE
    service = TicketService(gateway)
    with pytest.raises(TicketServiceError):
        service.create(_ticket())


def test_get_ticket():
    gateway = FakeGateway({"get_ticket": {"ticket_key": "TKT-2026-00014", "status": "IN_PROGRESS"}})
    service = TicketService(gateway)
    data = service.get("TKT-2026-00014")
    assert data["status"] == "IN_PROGRESS"


def test_list_for_customer():
    gateway = FakeGateway(
        {"list_customer_tickets": {"items": [{"ticket_key": "TKT-2026-00001"}], "total": 1}}
    )
    service = TicketService(gateway)
    tickets = service.list_for_customer("NH-100042")
    assert tickets == [{"ticket_key": "TKT-2026-00001"}]


def test_find_for_incident_avoids_a_duplicate_ticket():
    gateway = FakeGateway(
        {"find_tickets_by_incident": {"items": [{"ticket_key": "TKT-2026-00002"}]}}
    )
    service = TicketService(gateway)
    tickets = service.find_for_incident("INC-2026-003")
    assert len(tickets) == 1
    assert tickets[0]["ticket_key"] == "TKT-2026-00002"
