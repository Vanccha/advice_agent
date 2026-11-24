"""`mcp-notification` entrypoint: `uvicorn app.main:app`.

Where this sits: process entry point of the `mcp-notification` container.

Key ideas:
- MCP server exposes tools with JSON schemas at `POST /mcp`, plus `/health`
  and `/metrics`.

Read next: `tools.py`.
"""
from __future__ import annotations

from common.server import build_server

from .deps import settings
from .tools import register_tools

_settings = settings()
app = build_server(_settings.MCP_SERVER_NAME, "1.0.0", register_tools)
