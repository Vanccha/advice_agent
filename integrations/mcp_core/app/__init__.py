"""`mcp-core` adapter: tools over core-api (customers, subscriptions, provisioning, incidents).

Where this sits: one of five MCP servers in `integrations/`. Reads come from
the `diag.*` views, writes go through core-api's REST API.

Read next: `main.py`, then `tools_read.py` and `tools_write.py`.
"""
