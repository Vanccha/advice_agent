"""Shared plumbing for the integration layer: everything every MCP adapter needs.

Where this sits: imported by each `integrations/mcp_*` server. It is the only
code allowed to know how to talk to company systems in a generic way; the
assistant never imports it (it reaches the adapters over MCP).

Key ideas:
- Adapter / anti-corruption layer: company quirks stop here, behind one uniform
  tool surface, so a new customer only means new adapters.
- One envelope (`ToolResult`) and one registration helper (`register_tool`)
  make every tool on every server look the same.
- Reads and writes use different doors: SQL (read-only) versus REST (the
  company's own rules).

Read next: `result.py` (the envelope), then `http.py`, `readonly_db.py`,
`tool_spec.py` and `server.py`.
"""
