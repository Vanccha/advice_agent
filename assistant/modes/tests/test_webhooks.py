from __future__ import annotations

from core_common.models import AlertEvent
from core_common.types import Mode
from modes.tests.conftest import build_orchestrator, make_gateway


def test_ticket_status_webhook_produces_user_notice(tenant_config, session_factory) -> None:
    gateway = make_gateway({
        "detect_duplicate_charges": {"duplicates": [{"payment_ids": [88, 89]}]},
        "create_structured_ticket": {
            "ticket_key": "TKT-2026-00050", "department": "BILLING", "status": "NEW",
            "priority": "HIGH", "created": True,
        },
    })
    orch = build_orchestrator(tenant_config, session_factory, gateway=gateway)
    turn = orch.handle_message(
        conversation_id=None, customer_no="NS-100001", message="There is a wrong charge on my bill, I was charged twice"
    )
    assert turn.ticket_key == "TKT-2026-00050"

    event = {
        "event": "ticket.status_changed", "ticket_key": "TKT-2026-00050",
        "old_status": "NEW", "new_status": "IN_PROGRESS",
    }
    result = orch.handle_ticket_event(event)
    assert result is not None
    assert result.conversation_id == turn.conversation_id
    assert "TKT-2026-00050" in result.reply_en
    assert "in progress" in result.reply_en


def test_ticket_webhook_for_unknown_ticket_returns_none(tenant_config, session_factory) -> None:
    orch = build_orchestrator(tenant_config, session_factory)
    result = orch.handle_ticket_event({"ticket_key": "TKT-DOES-NOT-EXIST", "new_status": "RESOLVED"})
    assert result is None


def test_alert_webhook_records_event_and_creates_ticket(tenant_config, session_factory) -> None:
    gateway = make_gateway({
        "create_structured_ticket": {
            "ticket_key": "TKT-2026-00077", "department": "TECHNICAL_INFRA", "status": "NEW",
            "priority": "URGENT", "created": True,
        },
    })
    orch = build_orchestrator(tenant_config, session_factory, gateway=gateway)
    alert = {
        "alertname": "PaymentGatewayDown", "status": "firing", "severity": "critical",
        "department": "TECHNICAL_INFRA", "fingerprint": "fp-abc123",
        "summary": "payment gateway unreachable",
    }
    result = orch.handle_alert(alert)
    assert result is not None
    assert result.mode == Mode.ESCALATED.value
    assert result.ticket_key == "TKT-2026-00077"
    assert "the payment system is temporarily unavailable" in result.reply_en.lower()
    assert any(name == "post_department_message" for name, _ in gateway.calls_made)

    with session_factory() as session:
        rows = session.query(AlertEvent).filter_by(alert_fingerprint="fp-abc123").all()
        assert len(rows) == 1
        assert rows[0].handled is True


def test_non_actionable_alert_is_recorded_but_not_escalated(tenant_config, session_factory) -> None:
    orch = build_orchestrator(tenant_config, session_factory)
    result = orch.handle_alert({
        "alertname": "MissedInstallations", "status": "firing", "severity": "warning",
        "fingerprint": "fp-xyz",
    })
    assert result is None
    with session_factory() as session:
        rows = session.query(AlertEvent).filter_by(alert_fingerprint="fp-xyz").all()
        assert len(rows) == 1
        assert rows[0].handled is True
