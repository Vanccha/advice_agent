"""`mcp-payment` entrypoint: `uvicorn app.main:app`.

Where this sits: process entry point of the `mcp-payment` container.

Key ideas:
- MCP server exposes tools with JSON schemas: the four tools in
  docs/contracts.md section 3 at `POST /mcp`, plus `/health` and `/metrics`.

Read next: `tools.py`.
"""
from __future__ import annotations

from common.server import build_server

from .deps import settings
from .tools import register_tools

_settings = settings()
app = build_server(_settings.MCP_SERVER_NAME, "1.0.0", register_tools)
