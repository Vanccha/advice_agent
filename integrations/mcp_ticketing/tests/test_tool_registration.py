"""Every mcp-ticketing tool from docs/contracts.md §3 is registered with a
valid, flat input schema and a `ToolResult[...]` output schema.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from app.tools import register_tools

EXPECTED_TOOL_NAMES = {
    "create_structured_ticket",
    "get_ticket",
    "list_customer_tickets",
    "find_tickets_by_incident",
    "add_ticket_comment",
}


async def _build() -> MCPServer:
    server = MCPServer(name="mcp-ticketing-test", version="0.0.1")
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


async def test_create_structured_ticket_input_is_flat_and_covers_section_4_1() -> None:
    mcp = await _build()
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["create_structured_ticket"].input_schema
    assert set(schema["properties"]) == {
        "department",
        "issue_type",
        "priority",
        "subject",
        "body",
        "source",
        "external_ref",
        "incident_ref",
        "requester",
        "evidence",
        "attempted_steps",
        "affected_customers",
        "suggested_next_step",
        "urgency_reason",
    }
    assert set(schema.get("required", [])) == {"department", "issue_type", "priority", "subject", "body"}


async def test_get_ticket_requires_ticket_key() -> None:
    mcp = await _build()
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["get_ticket"].input_schema
    assert schema.get("required", []) == ["ticket_key"]


async def test_find_tickets_by_incident_maps_incident_no() -> None:
    mcp = await _build()
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["find_tickets_by_incident"].input_schema
    assert "incident_no" in schema["properties"]
