"""`mcp-monitoring` entrypoint: `uvicorn app.main:app`.

Exposes the three tools listed for `mcp-monitoring` in docs/contracts.md §3 over MCP
streamable HTTP at `POST /mcp`, plus `GET /health`, `GET /metrics` and the one plain
(non-MCP) HTTP endpoint this adapter needs: `POST /webhooks/alertmanager`, which the
company's Alertmanager posts to. That route is passed to `build_server` as
`extra_routes` so it is registered before the MCP app's catch-all mount at "/".
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
