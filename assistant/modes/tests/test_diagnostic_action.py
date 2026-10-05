from __future__ import annotations

from core_common.types import Mode
from modes.tests.conftest import build_orchestrator, make_gateway

COMPLAINT = "İnternetim hala açılmadı, bir sorun var"


def test_stuck_provisioning_is_fixed_without_a_ticket(tenant_config, session_factory) -> None:
    gateway = make_gateway({
        "get_subscription_status": {
            "customer_no": "NH-100001", "subscription_id": 42, "package_code": "FIBER_100_TEMEL",
            "status": "provisioning", "monthly_price_try": 349.0, "region_code": "IST-KAD",
        },
        "get_provisioning_status": {"job_id": 7, "status": "stuck", "attempt_count": 1, "is_stuck": True},
    })
    orch = build_orchestrator(tenant_config, session_factory, gateway=gateway)
    result = orch.handle_message(conversation_id=None, customer_no="NH-100001", message=COMPLAINT)

    assert result.diagnosis["root_cause"] == "stuck_provisioning"
    assert result.mode == Mode.CLOSING.value
    assert result.ticket_key is None
    assert ("retry_provisioning_job", {"customer_no": "NH-100001", "subscription_id": 42, "job_id": 7}) in gateway.calls_made


def test_double_charge_is_refused_and_creates_billing_ticket(tenant_config, session_factory) -> None:
    gateway = make_gateway({
        "detect_duplicate_charges": {"duplicates": [{"payment_ids": [88, 89]}]},
        "create_structured_ticket": {
            "ticket_key": "TKT-2026-00099", "department": "BILLING", "status": "NEW",
            "priority": "HIGH", "created": True,
        },
    })
    orch = build_orchestrator(tenant_config, session_factory, gateway=gateway)
    result = orch.handle_message(conversation_id=None, customer_no="NH-100001", message=COMPLAINT)

    assert result.diagnosis["root_cause"] == "double_charge"
    assert result.mode == Mode.ESCALATED.value
    assert result.ticket_key == "TKT-2026-00099"

    created_calls = [args for name, args in gateway.calls_made if name == "create_structured_ticket"]
    assert len(created_calls) == 1
    ticket_payload = created_calls[0]
    assert ticket_payload["department"] == "BILLING"
    assert set(ticket_payload["evidence"]["record_ids"].get("payment_ids", [])) == {88, 89}
    blocked = [s for s in ticket_payload["attempted_steps"] if s["outcome"] == "blocked"]
    assert blocked, "the refused refund attempt must appear in attempted_steps"


def test_regional_incident_two_customers_share_one_ticket(tenant_config, session_factory) -> None:
    incident = {
        "incident_no": "INC-2026-014", "region_code": "IST-KAD", "severity": "critical",
        "status": "open", "title": "Bölgesel kesinti",
    }
    gateway = make_gateway({"get_active_incidents_for_region": {"items": [incident]}})
    orch = build_orchestrator(tenant_config, session_factory, gateway=gateway)

    result1 = orch.handle_message(conversation_id=None, customer_no="NH-100001", message=COMPLAINT)
    assert result1.diagnosis["root_cause"] == "regional_outage"
    ticket_key_1 = result1.ticket_key
    assert ticket_key_1 is not None

    # Second customer: the gateway must now report the ticket just "created" so
    # find_for_incident resolves to it instead of creating a second one.
    gateway.set_response(
        "find_tickets_by_incident",
        {"items": [{"ticket_key": ticket_key_1, "department": "TECHNICAL_INFRA", "status": "NEW"}]},
    )
    result2 = orch.handle_message(conversation_id=None, customer_no="NH-100002", message=COMPLAINT)
    assert result2.ticket_key == ticket_key_1

    created_calls = [name for name, _ in gateway.calls_made if name == "create_structured_ticket"]
    assert len(created_calls) == 1, "only one ticket may exist for the incident"
    comment_calls = [name for name, _ in gateway.calls_made if name == "add_ticket_comment"]
    assert len(comment_calls) == 1


def test_outage_credit_requires_approval_then_executes(tenant_config, session_factory) -> None:
    incident = {
        "incident_no": "INC-2026-020", "region_code": "IST-KAD", "severity": "critical",
        "status": "open", "title": "Bölgesel kesinti",
    }
    gateway = make_gateway({"get_active_incidents_for_region": {"items": [incident]}})
    orch = build_orchestrator(tenant_config, session_factory, gateway=gateway)

    result = orch.handle_message(conversation_id=None, customer_no="NH-100001", message=COMPLAINT)
    assert result.mode == Mode.AWAITING_APPROVAL.value
    assert result.requires_approval is True
    assert result.approval_id is not None
    assert not any(name == "apply_outage_credit" for name, _ in gateway.calls_made)

    approved = orch.resolve_approval(approval_id=result.approval_id, granted=True)
    assert approved.mode == Mode.CLOSING.value
    assert any(name == "apply_outage_credit" for name, _ in gateway.calls_made)
