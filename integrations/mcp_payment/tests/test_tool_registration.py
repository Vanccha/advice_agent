"""Every mcp-payment tool from docs/contracts.md §3 is registered with a
valid, flat input schema and a `ToolResult[...]` output schema."""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from app.tools import register_tools

EXPECTED_TOOL_NAMES = {
    "get_payment_status",
    "list_customer_charges",
    "get_gateway_health",
    "detect_duplicate_charges",
}


async def _build() -> MCPServer:
    server = MCPServer(name="mcp-payment-test", version="0.0.1")
    register_tools(server)
    return server


async def test_every_contract_tool_is_registered() -> None:
    mcp = await _build()
    tools = await mcp.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOL_NAMES


async def test_every_tool_has_object_input_and_output_schemas() -> None:
    mcp = await _build()
    tools = await mcp.list_tools()
    for tool in tools:
        assert tool.input_schema.get("type") == "object", tool.name
        assert tool.description and "\n" not in tool.description
        assert tool.output_schema is not None
        assert set(tool.output_schema.get("properties", {})) >= {"ok", "data", "error", "source"}


async def test_get_gateway_health_takes_no_arguments() -> None:
    mcp = await _build()
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["get_gateway_health"].input_schema
    assert schema.get("properties", {}) == {}
