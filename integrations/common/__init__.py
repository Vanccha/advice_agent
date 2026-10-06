"""Shared adapter plumbing for the NetSwift integration layer (one MCP server per
company system). Only this package may import company-neutral helpers used by
every `integrations/mcp_*` server: settings, the `ToolResult` envelope, the
company REST client, the read-only diagnostic DB access, the FastAPI+MCP
server factory and the tool-registration helper.
"""
