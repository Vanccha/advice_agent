import pytest
from sqlalchemy.orm import sessionmaker

from audit.log import AuditLog
from core_common.db import bootstrap_schema, create_sqlite_engine
from mcp_gateway.fake import FakeGateway
from mcp_gateway.types import ToolBudgetExceeded


@pytest.fixture()
def audit_log() -> AuditLog:
    engine = create_sqlite_engine()
    bootstrap_schema(engine)
    sm = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return AuditLog(sm)


async def test_call_returns_canned_response(audit_log: AuditLog):
    gateway = FakeGateway({"find_customer": {"customer_no": "NH-100042"}}, audit_log)
    outcome = await gateway.call("find_customer", {"customer_no": "NH-100042"})
    assert outcome.ok is True
    assert outcome.data == {"customer_no": "NH-100042"}


async def test_unknown_tool_is_adapter_unavailable_not_an_exception(audit_log: AuditLog):
    gateway = FakeGateway({}, audit_log)
    outcome = await gateway.call("no_such_tool", {})
    assert outcome.ok is False
    assert outcome.error_code == "ADAPTER_UNAVAILABLE"


async def test_budget_exceeded_raises_on_call_n_plus_1(audit_log: AuditLog):
    gateway = FakeGateway({"x": {"ok": True}}, audit_log, max_calls_per_turn=2)
    await gateway.call("x", {})
    await gateway.call("x", {})
    with pytest.raises(ToolBudgetExceeded):
        await gateway.call("x", {})


async def test_reset_turn_budget_allows_more_calls(audit_log: AuditLog):
    gateway = FakeGateway({"x": {"ok": True}}, audit_log, max_calls_per_turn=1)
    await gateway.call("x", {})
    with pytest.raises(ToolBudgetExceeded):
        await gateway.call("x", {})
    gateway.reset_turn_budget()
    outcome = await gateway.call("x", {})
    assert outcome.ok is True


async def test_every_call_writes_an_audit_entry_with_a_digest_not_raw_output(audit_log: AuditLog):
    gateway = FakeGateway(
        {"find_customer": {"customer_no": "NH-100042", "phone": "0532 111 22 31"}},
        audit_log,
        conversation_id="conv-mcp-1",
    )
    await gateway.call("find_customer", {"customer_no": "NH-100042"})
    timeline = audit_log.timeline("conv-mcp-1")
    assert len(timeline) == 1
    entry = timeline[0]
    assert entry.tool_name == "find_customer"
    assert entry.tool_output_digest is not None
    assert len(entry.tool_output_digest) == 64
    assert "0532 111 22 31" not in str(entry.tool_input)
    assert "0532 111 22 31" not in str(entry.evidence)


async def test_catalog_groups_by_adapter(audit_log: AuditLog):
    gateway = FakeGateway(
        {"find_customer": {}, "get_payment_status": {}},
        audit_log,
        adapter_map={"find_customer": "core", "get_payment_status": "payment"},
    )
    catalog = await gateway.catalog()
    assert set(catalog.keys()) == {"core", "payment"}
    assert catalog["core"][0].name == "find_customer"


def test_call_sync_works_without_an_event_loop(audit_log: AuditLog):
    gateway = FakeGateway({"find_customer": {"ok": True}}, audit_log)
    outcome = gateway.call_sync("find_customer", {})
    assert outcome.ok is True
