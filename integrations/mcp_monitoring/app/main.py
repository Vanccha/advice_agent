"""`mcp-monitoring` entrypoint: `uvicorn app.main:app`.

Where this sits: process entry point of the `mcp-monitoring` container.

Key ideas:
- MCP server exposes three tools with JSON schemas at `POST /mcp`, plus
  `/health`, `/metrics` and one plain HTTP endpoint,
  `POST /webhooks/alertmanager`.
- That route is passed as `extra_routes` so it is registered before the MCP
  app's catch-all mount at "/".

Read next: `webhooks.py`, `tools.py`.
"""
from __future__ import annotations

from common.server import build_server

from . import webhooks
from .deps import settings
from .tools import register_tools

_settings = settings()

app = build_server(
    name=_settings.MCP_SERVER_NAME,
    version="1.0.0",
    tools_registrar=register_tools,
    extra_routes=webhooks.router,
)
