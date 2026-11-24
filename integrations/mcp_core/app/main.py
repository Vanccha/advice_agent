"""`mcp-core` entrypoint: `uvicorn app.main:app`.

Where this sits: the process entry point of the `mcp-core` container; the
assistant connects to it over MCP streamable HTTP.

Key ideas:
- MCP server exposes tools with JSON schemas: 11 read-only and 5 mutating
  tools (docs/contracts.md section 3) at `POST /mcp`, plus `GET /health` and
  `GET /metrics`.
- All wiring lives in `common.server.build_server`; this file only lists
  which tools to register.

Read next: `tools_read.py`, `tools_write.py`.
"""
from __future__ import annotations

from typing import Any

from common.server import build_server

from .deps import settings
from .tools_read import register_read_tools
from .tools_write import register_write_tools


def register_tools(mcp: Any) -> None:
    """Register every `mcp-core` tool on the MCP server."""
    register_read_tools(mcp)
    register_write_tools(mcp)


_settings = settings()
app = build_server(_settings.MCP_SERVER_NAME, "1.0.0", register_tools)
