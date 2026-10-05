"""`mcp-notification` entrypoint: `uvicorn app.main:app`.

Exposes the 2 tools listed for `mcp-notification` in docs/contracts.md §3
over MCP streamable HTTP at `POST /mcp`, plus `GET /health` and `GET
/metrics`.
"""
from __future__ import annotations

from common.server import build_server

from .deps import settings
from .tools import register_tools

_settings = settings()
app = build_server(_settings.MCP_SERVER_NAME, "1.0.0", register_tools)
