from __future__ import annotations

from core_common.types import Mode
from modes.tests.conftest import build_orchestrator, make_gateway


def test_complaint_routes_to_diagnostic(tenant_config, session_factory) -> None:
    gateway = make_gateway({
        "get_provisioning_status": {"job_id": 7, "status": "stuck", "attempt_count": 1, "is_stuck": True},
    })
    orch = build_orchestrator(tenant_config, session_factory, gateway=gateway)
    result = orch.handle_message(
        conversation_id=None, customer_no="NH-100001", message="İnternetim hala açılmadı, arıza var"
    )
    # DIAGNOSTIC auto-advances into ACTION within the same turn (fixed checklist, no
    # further user input needed) and the stuck job gets fixed without a ticket.
    assert result.mode == Mode.CLOSING.value
    assert result.ticket_key is None
    assert result.diagnosis["root_cause"] == "stuck_provisioning"


def test_advisory_request_routes_to_advisory(tenant_config, session_factory) -> None:
    orch = build_orchestrator(tenant_config, session_factory)
    result = orch.handle_message(
        conversation_id=None, customer_no="NH-100001", message="Bana uygun bir paket önerir misiniz?"
    )
    assert result.mode == Mode.ADVISORY.value
    assert "kullanıyorsunuz" in result.reply_tr or "cihaz" in result.reply_tr or result.reply_tr


def test_low_confidence_intent_hands_over_instead_of_guessing(tenant_config, session_factory) -> None:
    from modes.tests.conftest import make_scripted_provider

    provider = make_scripted_provider([(r".*", "problem_report", 0.1)])
    orch = build_orchestrator(tenant_config, session_factory, provider=provider)
    result = orch.handle_message(conversation_id=None, customer_no="NH-100001", message="asdkjasndkj")
    assert result.mode == Mode.CLOSING.value
    assert "temsilci" in result.reply_tr.lower()
