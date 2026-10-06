from pathlib import Path

import pytest

from core_common.config import clear_tenant_config_cache, load_tenant_config
from core_common.types import Decision, Department, Intent, Priority
from decision.fallback import apply_confidence_floor

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
def routing_config(monkeypatch: pytest.MonkeyPatch):
    clear_tenant_config_cache()
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    cfg = load_tenant_config("netswift", config_dir=CONFIG_DIR)
    clear_tenant_config_cache()
    return cfg.routing


def test_high_confidence_decision_passes_through_unchanged(routing_config):
    decision = Decision(value=Department.BILLING, confidence=0.9, rationale="r", model="m", raw={})
    result = apply_confidence_floor(decision, 0.6, routing_config, issue_type="double_charge")
    assert result.escalated is False
    assert result.decision is decision


def test_low_confidence_department_routes_via_issue_routing_table(routing_config):
    decision = Decision(value=Department.TECHNICAL_INFRA, confidence=0.2, rationale="guess", model="m", raw={})
    result = apply_confidence_floor(decision, 0.6, routing_config, issue_type="double_charge")
    assert result.escalated is True
    # routing.yaml: issue_routing.double_charge -> BILLING (not the model's own guess)
    assert result.decision.value == Department.BILLING
    assert result.decision.confidence == 0.0


def test_low_confidence_urgency_is_raised_to_the_floor_never_lowered(routing_config):
    decision = Decision(value=Priority.LOW, confidence=0.1, rationale="guess", model="m", raw={})
    result = apply_confidence_floor(decision, 0.6, routing_config, issue_type="payment_system_down")
    assert result.escalated is True
    # routing.yaml: urgency_floor.payment_system_down -> URGENT
    assert result.decision.value == Priority.URGENT


def test_low_confidence_urgency_already_above_floor_is_kept(routing_config):
    decision = Decision(value=Priority.URGENT, confidence=0.1, rationale="guess", model="m", raw={})
    result = apply_confidence_floor(decision, 0.6, routing_config, issue_type="stuck_provisioning")
    assert result.escalated is True
    # floor for stuck_provisioning is NORMAL; URGENT already outranks it, so it is kept.
    assert result.decision.value == Priority.URGENT


def test_low_confidence_without_issue_type_just_escalates(routing_config):
    decision = Decision(value=Intent.ADVISORY, confidence=0.1, rationale="guess", model="m", raw={})
    result = apply_confidence_floor(decision, 0.6, routing_config)
    assert result.escalated is True
    assert result.decision is decision  # no table applies to Intent; unchanged but flagged
