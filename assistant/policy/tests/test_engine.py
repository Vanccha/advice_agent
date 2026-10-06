from pathlib import Path

import pytest

from core_common.config import clear_tenant_config_cache, load_tenant_config
from policy.engine import PolicyEngine

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config" / "tenants"

REQUIRED_ENV = {
    "MCP_CORE_URL": "http://mcp-core:8000/mcp",
    "MCP_PAYMENT_URL": "http://mcp-payment:8000/mcp",
    "MCP_TICKETING_URL": "http://mcp-ticketing:8000/mcp",
    "MCP_MONITORING_URL": "http://mcp-monitoring:8000/mcp",
    "MCP_NOTIFICATION_URL": "http://mcp-notification:8000/mcp",
}


@pytest.fixture()
def engine(monkeypatch: pytest.MonkeyPatch) -> PolicyEngine:
    clear_tenant_config_cache()
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    cfg = load_tenant_config("netswift", config_dir=CONFIG_DIR)
    clear_tenant_config_cache()
    return PolicyEngine(cfg.policy)


# (action_name, context, expected_allowed, expected_requires_confirmation,
#  expected_escalate_to, expected_reason_code)
MATRIX = [
    # retry_provisioning_job
    (
        "retry_provisioning_job",
        {"job": {"status": "stuck", "attempt_count": 1}},
        True, False, None, None,
    ),
    (
        "retry_provisioning_job",
        {"job": {"status": "stuck", "attempt_count": 3}},
        False, False, "SUBSCRIPTION_OPS", "provisioning_retry_limit",
    ),
    (
        "retry_provisioning_job",
        {"job": {"status": "active", "attempt_count": 0}},
        False, False, "SUBSCRIPTION_OPS", "provisioning_retry_limit",
    ),
    # resend_activation_notification
    (
        "resend_activation_notification",
        {"usage": {"per_conversation": 0, "per_customer_per_day": 0}},
        True, False, None, None,
    ),
    (
        "resend_activation_notification",
        {"usage": {"per_conversation": 2, "per_customer_per_day": 0}},
        False, False, None, "rate_limit_exceeded",
    ),
    (
        "resend_activation_notification",
        {"usage": {"per_conversation": 0, "per_customer_per_day": 5}},
        False, False, None, "rate_limit_exceeded",
    ),
    # enqueue_provisioning_job (no on_condition_fail configured -> generic fallback)
    (
        "enqueue_provisioning_job",
        {"subscription": {"status": "payment_received"}, "payment": {"status": "succeeded"}},
        True, False, None, None,
    ),
    (
        "enqueue_provisioning_job",
        {"subscription": {"status": "active"}, "payment": {"status": "succeeded"}},
        False, False, None, "condition_failed",
    ),
    # apply_outage_credit
    (
        "apply_outage_credit",
        {"incident": {"exists": True}, "credit": {"existing_count_30d": 0}, "amount_gbp": 5},
        True, True, None, None,
    ),
    (
        "apply_outage_credit",
        {"incident": {"exists": True}, "credit": {"existing_count_30d": 0}, "amount_gbp": 5.1},
        False, False, None, "amount_above_limit",
    ),
    (
        "apply_outage_credit",
        {"incident": {"exists": True}, "credit": {"existing_count_30d": 1}, "amount_gbp": 5},
        False, False, "BILLING", "credit_not_applicable",
    ),
    (
        "apply_outage_credit",
        {"incident": {"exists": False}, "credit": {"existing_count_30d": 0}, "amount_gbp": 1},
        False, False, "BILLING", "credit_not_applicable",
    ),
    # hard-denied, escalation-only actions
    ("issue_refund", {}, False, False, "BILLING", "refund_not_permitted"),
    ("change_package", {}, False, False, "SUBSCRIPTION_OPS", "plan_change_not_permitted"),
    ("reschedule_installation", {}, False, False, "FIELD_INSTALL", "field_scheduling_not_permitted"),
    ("repair_infrastructure", {}, False, False, "TECHNICAL_INFRA", "infrastructure_not_permitted"),
    ("cancel_subscription", {}, False, False, "SUBSCRIPTION_OPS", "cancellation_not_permitted"),
    # allowed, no confirmation, no conditions
    ("send_department_message", {}, True, False, None, None),
    # unknown action
    ("teleport_customer", {}, False, False, None, "action_not_in_policy"),
]


@pytest.mark.parametrize(
    "action_name,context,expected_allowed,expected_confirm,expected_escalate,expected_reason",
    MATRIX,
)
def test_policy_matrix(
    engine: PolicyEngine,
    action_name,
    context,
    expected_allowed,
    expected_confirm,
    expected_escalate,
    expected_reason,
):
    decision = engine.check(action_name, context)

    assert decision.allowed is expected_allowed
    assert decision.requires_confirmation is expected_confirm
    if expected_escalate is not None:
        assert decision.escalate_to == expected_escalate
    if expected_reason is not None:
        assert decision.reason_code == expected_reason
    assert decision.reason_en  # every decision carries a customer-facing sentence


def test_unknown_action_is_denied_by_default(engine: PolicyEngine):
    decision = engine.check("some_action_nobody_configured", {})
    assert decision.allowed is False
    assert decision.reason_code == "action_not_in_policy"


def test_describe_allowed_actions_matches_policy_file(engine: PolicyEngine):
    allowed = engine.describe_allowed_actions()
    assert "retry_provisioning_job" in allowed
    assert "issue_refund" not in allowed


def test_explicit_denial_reason_en_comes_from_policy_file(engine: PolicyEngine):
    decision = engine.check("issue_refund", {})
    assert decision.reason_en == "Refunds need approval from the Billing team."
