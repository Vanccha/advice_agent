from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from audit.log import AuditLog
from core_common.config import clear_tenant_config_cache, load_tenant_config
from core_common.db import bootstrap_schema, create_sqlite_engine
from policy.engine import PolicyEngine
from policy.executor import ActionExecutor, ConfirmationRequired, PolicyDenied

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config" / "tenants"

REQUIRED_ENV = {
    "MCP_CORE_URL": "http://mcp-core:8000/mcp",
    "MCP_PAYMENT_URL": "http://mcp-payment:8000/mcp",
    "MCP_TICKETING_URL": "http://mcp-ticketing:8000/mcp",
    "MCP_MONITORING_URL": "http://mcp-monitoring:8000/mcp",
    "MCP_NOTIFICATION_URL": "http://mcp-notification:8000/mcp",
}


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, action_name: str, params: dict) -> dict:
        self.calls.append((action_name, params))
        return {"status": "done", "action": action_name}


@pytest.fixture()
def policy_engine(monkeypatch: pytest.MonkeyPatch) -> PolicyEngine:
    clear_tenant_config_cache()
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    cfg = load_tenant_config("netswift", config_dir=CONFIG_DIR)
    clear_tenant_config_cache()
    return PolicyEngine(cfg.policy)


@pytest.fixture()
def audit_log() -> AuditLog:
    engine = create_sqlite_engine()
    bootstrap_schema(engine)
    sm = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return AuditLog(sm)


def test_policy_denied_prevents_runner_from_running(policy_engine, audit_log):
    runner = RecordingRunner()
    executor = ActionExecutor(policy_engine, audit_log, runner)

    with pytest.raises(PolicyDenied) as exc_info:
        executor.execute(
            "issue_refund",
            {"payment_id": 88, "amount_gbp": 10},
            {"conversation_id": "conv-exec-1", "tenant": "netswift"},
        )

    assert exc_info.value.decision.escalate_to == "BILLING"
    assert runner.calls == []  # the action runner was never invoked

    entries = audit_log.timeline("conv-exec-1")
    assert any(e.step_type == "policy_check" for e in entries)


def test_confirmation_required_then_confirmed_run_succeeds(policy_engine, audit_log):
    runner = RecordingRunner()
    executor = ActionExecutor(policy_engine, audit_log, runner)
    context = {
        "conversation_id": "conv-exec-2",
        "tenant": "netswift",
        "incident": {"exists": True},
        "credit": {"existing_count_30d": 0},
        "amount_gbp": 5,
    }

    with pytest.raises(ConfirmationRequired):
        executor.execute("apply_outage_credit", {"amount_gbp": 5}, context, confirmed=False)
    assert runner.calls == []

    result = executor.execute("apply_outage_credit", {"amount_gbp": 5}, context, confirmed=True)
    assert result == {"status": "done", "action": "apply_outage_credit"}
    assert runner.calls == [("apply_outage_credit", {"amount_gbp": 5})]

    entries = audit_log.timeline("conv-exec-2")
    step_types = [e.step_type for e in entries]
    assert "approval_requested" in step_types
    assert "action" in step_types


def test_allowed_action_without_confirmation_runs_immediately(policy_engine, audit_log):
    runner = RecordingRunner()
    executor = ActionExecutor(policy_engine, audit_log, runner)
    context = {
        "conversation_id": "conv-exec-3",
        "tenant": "netswift",
        "job": {"status": "stuck", "attempt_count": 0},
    }

    result = executor.execute("retry_provisioning_job", {"job_id": 7}, context)
    assert result["status"] == "done"
    assert runner.calls == [("retry_provisioning_job", {"job_id": 7})]

    verification = audit_log.verify_chain("conv-exec-3")
    assert verification.ok is True
