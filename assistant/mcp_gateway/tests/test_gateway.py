import pytest
from sqlalchemy.orm import sessionmaker

from audit.log import AuditLog
from core_common.config import AdapterConfig
from core_common.db import bootstrap_schema, create_sqlite_engine
from mcp_gateway.gateway import ToolGateway
from mcp_gateway.types import ToolBudgetExceeded

# Nothing listens here: connection is refused immediately, so these tests stay fast
# without needing any live mcp-* container.
DEAD_ADAPTER = AdapterConfig(
    transport="mcp_streamable_http", url="http://127.0.0.1:59999/mcp", required=False
)


@pytest.fixture()
def audit_log() -> AuditLog:
    engine = create_sqlite_engine()
    bootstrap_schema(engine)
    sm = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return AuditLog(sm)


async def test_call_against_a_dead_adapter_reports_adapter_unavailable_never_raises(
    audit_log: AuditLog,
):
    gateway = ToolGateway(
        {"core": DEAD_ADAPTER}, audit_log, conversation_id="conv-1", tenant="netswift"
    )
    outcome = await gateway.call("find_customer", {"customer_no": "NS-100042"})
    assert outcome.ok is False
    assert outcome.error_code == "ADAPTER_UNAVAILABLE"


async def test_budget_exceeded_counts_even_failed_calls(audit_log: AuditLog):
    gateway = ToolGateway(
        {"core": DEAD_ADAPTER},
        audit_log,
        conversation_id="conv-2",
        tenant="netswift",
        max_calls_per_turn=1,
    )
    await gateway.call("find_customer", {})
    with pytest.raises(ToolBudgetExceeded):
        await gateway.call("find_customer", {})


async def test_every_call_writes_an_audit_entry_with_digest(audit_log: AuditLog):
    gateway = ToolGateway(
        {"core": DEAD_ADAPTER}, audit_log, conversation_id="conv-3", tenant="netswift"
    )
    await gateway.call("find_customer", {"customer_no": "NS-100042"})
    timeline = audit_log.timeline("conv-3")
    assert len(timeline) == 1
    assert timeline[0].tool_name == "find_customer"
    assert timeline[0].tool_output_digest is not None
    assert len(timeline[0].tool_output_digest) == 64


async def test_empty_adapters_dict_means_every_call_is_unavailable(audit_log: AuditLog):
    gateway = ToolGateway({}, audit_log, conversation_id="conv-4", tenant="netswift")
    outcome = await gateway.call("anything", {})
    assert outcome.ok is False
    assert outcome.error_code == "ADAPTER_UNAVAILABLE"


def test_from_tenant_config_reads_limits_from_policy(monkeypatch: pytest.MonkeyPatch, audit_log: AuditLog):
    from pathlib import Path

    from core_common.config import clear_tenant_config_cache, load_tenant_config

    repo_root = Path(__file__).resolve().parents[3]
    config_dir = repo_root / "config" / "tenants"
    for key in (
        "MCP_CORE_URL",
        "MCP_PAYMENT_URL",
        "MCP_TICKETING_URL",
        "MCP_MONITORING_URL",
        "MCP_NOTIFICATION_URL",
    ):
        monkeypatch.setenv(key, "http://127.0.0.1:59999/mcp")
    clear_tenant_config_cache()
    tenant_config = load_tenant_config("netswift", config_dir=config_dir)
    clear_tenant_config_cache()

    gateway = ToolGateway.from_tenant_config(tenant_config, audit_log, conversation_id="conv-5")
    assert gateway.max_calls_per_turn == tenant_config.policy.limits.max_tool_calls_per_turn
    assert set(gateway._adapters.keys()) == {"core", "payment", "ticketing", "monitoring", "notification"}
