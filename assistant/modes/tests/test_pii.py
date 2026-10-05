from __future__ import annotations

from privacy.masking import PIILeakError, assert_no_pii
from modes.tests.conftest import build_orchestrator, make_gateway


def test_no_raw_pii_in_audit_trail_or_ticket(tenant_config, session_factory) -> None:
    gateway = make_gateway({
        "detect_duplicate_charges": {"duplicates": [{"payment_ids": [88, 89]}]},
        "create_structured_ticket": {
            "ticket_key": "TKT-2026-00061", "department": "BILLING", "status": "NEW",
            "priority": "HIGH", "created": True,
        },
    })
    orch = build_orchestrator(tenant_config, session_factory, gateway=gateway)
    result = orch.handle_message(
        conversation_id=None, customer_no="NH-100001",
        message="Faturamda yanlış tahsilat var, iki kere çekilmiş, lütfen bakın 05551112233",
    )
    assert result.ticket_key == "TKT-2026-00061"

    entries = orch.audit_log.timeline(result.conversation_id)
    assert entries, "the turn must have produced audit entries"
    for entry in entries:
        try:
            assert_no_pii(entry.evidence)
            if entry.tool_input is not None:
                assert_no_pii(entry.tool_input)
        except PIILeakError as exc:  # pragma: no cover - failure path
            raise AssertionError(f"raw PII leaked into audit entry {entry.step_type}: {exc}") from exc

    created_calls = [args for name, args in gateway.calls_made if name == "create_structured_ticket"]
    assert len(created_calls) == 1
    assert_no_pii(created_calls[0])  # the ticket payload itself must already be masked
