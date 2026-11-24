"""Rate limits and the per-conversation action cap are fed from the assistant's own records.

The executor, not the caller, supplies the usage counters, so these tests run actions through
a real `ActionExecutor` and check what the second, third... call does.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from audit.log import AuditLog
from core_common.config import clear_tenant_config_cache, load_tenant_config
from core_common.db import bootstrap_schema, create_sqlite_engine, session_scope
from core_common.models import ActionRecord, Conversation
from modes.machine import LimitExceeded, StateMachine
from policy.engine import PolicyEngine
from policy.executor import ActionExecutor, PolicyDenied
from policy.usage import action_usage

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "tenants"
REQUIRED_ENV = {
    "MCP_CORE_URL": "http://mcp-core:8000/mcp",
    "MCP_PAYMENT_URL": "http://mcp-payment:8000/mcp",
    "MCP_TICKETING_URL": "http://mcp-ticketing:8000/mcp",
    "MCP_MONITORING_URL": "http://mcp-monitoring:8000/mcp",
    "MCP_NOTIFICATION_URL": "http://mcp-notification:8000/mcp",
}
RESEND = "resend_activation_notification"


@pytest.fixture()
def tenant_config(monkeypatch: pytest.MonkeyPatch):
    clear_tenant_config_cache()
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    cfg = load_tenant_config("netswift", config_dir=CONFIG_DIR)
    clear_tenant_config_cache()
    return cfg


@pytest.fixture()
def session_factory() -> sessionmaker:
    engine = create_sqlite_engine()
    bootstrap_schema(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _executor(tenant_config, session_factory, guard=None) -> ActionExecutor:
    return ActionExecutor(
        PolicyEngine(tenant_config.policy),
        AuditLog(session_factory),
        lambda name, params: {"ok": True},
        conversation_action_guard=guard,
    )


def _run(executor: ActionExecutor, conversation_id: str, customer_no: str = "NS-1") -> None:
    executor.execute(
        RESEND, {"customer_no": customer_no}, {"conversation_id": conversation_id, "tenant": "netswift"}
    )


def test_per_conversation_rate_limit_stops_the_third_resend(tenant_config, session_factory) -> None:
    executor = _executor(tenant_config, session_factory)
    _run(executor, "conv-a")
    _run(executor, "conv-a")
    with pytest.raises(PolicyDenied) as exc:
        _run(executor, "conv-a")
    assert exc.value.decision.reason_code == "rate_limit_exceeded"
    assert exc.value.decision.limit_applied == "per_conversation"


def test_a_caller_cannot_fake_the_usage_counters(tenant_config, session_factory) -> None:
    executor = _executor(tenant_config, session_factory)
    for _ in range(2):
        _run(executor, "conv-b")
    with pytest.raises(PolicyDenied):
        executor.execute(
            RESEND,
            {"customer_no": "NS-1"},
            {"conversation_id": "conv-b", "tenant": "netswift", "usage": {"per_conversation": 0}},
        )


def test_per_customer_per_day_counts_across_conversations(tenant_config, session_factory) -> None:
    executor = _executor(tenant_config, session_factory)
    limit = tenant_config.policy.actions[RESEND].rate_limit.per_customer_per_day
    for i in range(limit):
        # Each conversation stays under its own per-conversation limit; conversations are
        # linked to the customer the way `ConversationStore.save` links them after a turn.
        with session_scope(session_factory) as session:
            session.add(Conversation(conversation_id=f"conv-day-{i}", tenant="netswift", customer_no="NS-7"))
        _run(executor, f"conv-day-{i}", customer_no="NS-7")
    with pytest.raises(PolicyDenied) as exc:
        _run(executor, "conv-day-new", customer_no="NS-7")
    assert exc.value.decision.limit_applied == "per_customer_per_day"
    _run(executor, "conv-other", customer_no="NS-8")  # another customer is unaffected


def test_usage_ignores_denied_attempts_and_yesterday(session_factory) -> None:
    yesterday = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1, minutes=1)
    with session_scope(session_factory) as session:
        session.add(Conversation(conversation_id="c1", tenant="netswift", customer_no="NS-9"))
        session.add_all([
            ActionRecord(conversation_id="c1", action_name=RESEND, policy_allowed=False, executed=False),
            ActionRecord(conversation_id="c1", action_name=RESEND, policy_allowed=True, executed=True,
                         created_at=yesterday),
            ActionRecord(conversation_id="c1", action_name=RESEND, policy_allowed=True, executed=True),
        ])
    usage = action_usage(session_factory, conversation_id="c1", customer_no="NS-9", action_name=RESEND)
    assert usage == {"per_conversation": 2, "per_customer_per_day": 1}


def test_conversation_action_cap_refuses_before_running(tenant_config, session_factory) -> None:
    machine = StateMachine(tenant_config.policy.limits)
    executor = _executor(tenant_config, session_factory, machine.check_actions_per_conversation)
    cap = tenant_config.policy.limits.max_actions_per_conversation
    with session_scope(session_factory) as session:
        session.add_all([
            ActionRecord(conversation_id="conv-cap", action_name=f"a{i}", policy_allowed=True, executed=True)
            for i in range(cap)
        ])
    with pytest.raises(LimitExceeded):
        executor.execute(
            "retry_provisioning_job", {"job_id": 1},
            {"conversation_id": "conv-cap", "tenant": "netswift", "job": {"status": "stuck", "attempt_count": 0}},
        )
