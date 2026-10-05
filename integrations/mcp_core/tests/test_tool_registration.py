"""Every mcp-core tool from docs/contracts.md §3 is registered with a valid,
flat input schema and a `ToolResult[...]` output schema — checked in-process
(no network, no server) by building a real `MCPServer` and registering the
real tool set.
"""
from __future__ import annotations

import pytest
from mcp.server.mcpserver import MCPServer

from app.main import register_tools

EXPECTED_TOOL_NAMES = {
    "find_customer",
    "get_subscription_status",
    "get_subscription_timeline",
    "get_provisioning_status",
    "get_installation_status",
    "get_modem_status",
    "get_active_incidents_for_region",
    "get_incident_detail",
    "list_packages",
    "get_notification_history",
    "get_region_health",
    "retry_provisioning_job",
    "resend_activation_notification",
    "apply_outage_credit",
    "request_refund",
    "reschedule_installation",
}


@pytest.fixture
def mcp() -> MCPServer:
    server = MCPServer(name="mcp-core-test", version="0.0.1")
    register_tools(server)
    return server


async def test_every_contract_tool_is_registered(mcp: MCPServer) -> None:
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOL_NAMES


async def test_every_tool_name_is_snake_case(mcp: MCPServer) -> None:
    tools = await mcp.list_tools()
    for tool in tools:
        assert tool.name == tool.name.lower()
        assert " " not in tool.name


async def test_every_tool_has_a_one_line_description(mcp: MCPServer) -> None:
    tools = await mcp.list_tools()
    for tool in tools:
        assert tool.description, f"{tool.name} has no description"
        assert "\n" not in tool.description


async def test_every_tool_has_object_input_and_output_schemas(mcp: MCPServer) -> None:
    tools = await mcp.list_tools()
    for tool in tools:
        assert tool.input_schema.get("type") == "object", tool.name
        assert tool.output_schema is not None, f"{tool.name} has no output schema"
        assert tool.output_schema.get("type") == "object", tool.name
        # every tool's output is the {ok, data, error, source} envelope
        assert set(tool.output_schema.get("properties", {})) >= {"ok", "data", "error", "source"}


async def test_find_customer_input_schema_is_flat_not_nested(mcp: MCPServer) -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["find_customer"].input_schema
    # the bug this guards against: a single nested-object parameter instead of
    # flat customer_no/phone/email fields (see common/tool_spec.py docstring)
    assert set(schema["properties"]) == {"customer_no", "phone", "email"}
    assert schema.get("required", []) == []
