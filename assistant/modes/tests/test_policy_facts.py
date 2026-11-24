"""Policy conditions run on real facts: the credit history comes from the company, the amount
from the action's own params, and the conversation action cap ends a turn politely."""
from __future__ import annotations

from core_common.db import session_scope
from core_common.models import ActionRecord, PendingApproval
from core_common.types import Mode
from mcp_gateway.types import ToolCallOutcome
from modes import replies
from modes.tests.conftest import build_orchestrator, make_gateway

COMPLAINT = "My internet still has not been switched on, there is a problem"
INCIDENT = {
    "incident_no": "INC-2026-020", "region_code": "LDN-CAM", "severity": "critical",
    "status": "open", "title": "Regional outage",
}


def _outage_gateway(**overrides):
    return make_gateway({"get_active_incidents_for_region": {"items": [INCIDENT]}, **overrides})


def test_credit_is_offered_when_the_company_reports_no_recent_credit(tenant_config, session_factory) -> None:
    gateway = _outage_gateway()
    orch = build_orchestrator(tenant_config, session_factory, gateway=gateway)
    result = orch.handle_message(conversation_id=None, customer_no="NS-100001", message=COMPLAINT)

    assert result.mode == Mode.AWAITING_APPROVAL.value
    assert ("get_credit_history", {"customer_no": "NS-100001", "days": 30}) in gateway.calls_made
    with session_scope(session_factory) as session:
        row = session.query(PendingApproval).one()
        stored = row.params
    # The amount that was policy-checked is the amount that will be executed.
    assert stored["policy_context"]["amount_gbp"] == stored["action_params"]["amount_gbp"]
    assert stored["policy_context"]["credit"] == {"existing_count_30d": 0}


def test_second_credit_in_30_days_is_not_offered(tenant_config, session_factory) -> None:
    gateway = _outage_gateway(get_credit_history={"days": 30, "count": 1, "credits": [{}]})
    orch = build_orchestrator(tenant_config, session_factory, gateway=gateway)
    result = orch.handle_message(conversation_id=None, customer_no="NS-100001", message=COMPLAINT)

    assert result.requires_approval is False
    assert result.mode == Mode.ESCALATED.value
    assert not any(name == "apply_outage_credit" for name, _ in gateway.calls_made)


def test_unknown_credit_history_denies_instead_of_assuming_zero(tenant_config, session_factory) -> None:
    down = ToolCallOutcome(ok=False, error_code="ADAPTER_UNAVAILABLE", error_message="down")
    gateway = _outage_gateway(get_credit_history=down)
    orch = build_orchestrator(tenant_config, session_factory, gateway=gateway)
    result = orch.handle_message(conversation_id=None, customer_no="NS-100001", message=COMPLAINT)

    assert result.requires_approval is False
    assert not any(name == "apply_outage_credit" for name, _ in gateway.calls_made)


def test_turn_ends_politely_when_the_conversation_action_cap_is_reached(
    tenant_config, session_factory
) -> None:
    cap = tenant_config.policy.limits.max_actions_per_conversation
    with session_scope(session_factory) as session:
        session.add_all([
            ActionRecord(conversation_id="conv-full", action_name="send_department_message",
                         policy_allowed=True, executed=True)
            for _ in range(cap)
        ])
    gateway = make_gateway({
        "get_subscription_status": {
            "customer_no": "NS-100001", "subscription_id": 42, "package_code": "FIBER_100_BASIC",
            "status": "provisioning", "monthly_price_gbp": 34.9, "region_code": "LDN-CAM",
        },
        "get_provisioning_status": {"job_id": 7, "status": "stuck", "attempt_count": 1, "is_stuck": True},
    })
    orch = build_orchestrator(tenant_config, session_factory, gateway=gateway)
    result = orch.handle_message(conversation_id="conv-full", customer_no="NS-100001", message=COMPLAINT)

    assert result.reply_en == replies.STEP_LIMIT_REACHED
    assert not any(name == "retry_provisioning_job" for name, _ in gateway.calls_made)
