"""Every mcp-monitoring tool from docs/contracts.md §3 is registered with a
valid, flat input schema and a `ToolResult[...]` output schema. The plain
`POST /webhooks/alertmanager` HTTP endpoint is covered separately
(`test_webhook.py`) — it is not an MCP tool.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from app.tools import register_tools

EXPECTED_TOOL_NAMES = {
    "get_service_health",
    "query_metric",
    "list_active_alerts",
}


async def _build() -> MCPServer:
    server = MCPServer(name="mcp-monitoring-test", version="0.0.1")
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


async def test_get_service_health_takes_no_arguments() -> None:
    mcp = await _build()
    tools = {t.name: t for t in await mcp.list_tools()}
    assert tools["get_service_health"].input_schema.get("properties", {}) == {}


async def test_query_metric_requires_only_query() -> None:
    mcp = await _build()
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["query_metric"].input_schema
    assert set(schema["properties"]) == {"query", "time"}
    assert schema.get("required", []) == ["query"]


async def test_list_active_alerts_has_optional_filters() -> None:
    mcp = await _build()
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["list_active_alerts"].input_schema
    assert set(schema["properties"]) == {"severity", "department"}
    assert schema.get("required", []) == []
