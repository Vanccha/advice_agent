"""`mcp-core` entrypoint: `uvicorn app.main:app`.

Exposes the 11 read-only and 5 mutating tools listed for `mcp-core` in
docs/contracts.md §3 over MCP streamable HTTP at `POST /mcp`, plus `GET
/health` and `GET /metrics`.
"""
from __future__ import annotations

from typing import Any

from common.server import build_server

from .deps import settings
from .tools_read import register_read_tools
from .tools_write import register_write_tools


def register_tools(mcp: Any) -> None:
    register_read_tools(mcp)
    register_write_tools(mcp)


_settings = settings()
app = build_server(_settings.MCP_SERVER_NAME, "1.0.0", register_tools)
