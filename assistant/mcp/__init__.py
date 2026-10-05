"""The tool gateway: the only way the product touches the company (contracts §3).

Public surface:
    `ToolGateway` — live gateway over streamable-HTTP MCP adapters (`gateway.py`).
    `FakeGateway` — same surface, canned responses, no network (`fake.py`).
    `ToolSpec`, `ToolCallOutcome`, `ToolBudgetExceeded` — shared types (`types.py`).
    `make_action_runner` — wires `policy.executor.ActionExecutor` to a gateway
        (`action_runner.py`).

Note for maintainers: this package is importable as top-level `mcp`, which collides with
the pip-installed `mcp` client SDK of the same name (see `_sdk.py`'s module docstring for
why, and how that collision is worked around). Nothing in this package (or anywhere else
in `assistant/`) should ever do a bare ``import mcp`` expecting the third-party SDK —
always go through ``from mcp._sdk import ClientSession, streamable_http_client``.
"""
from __future__ import annotations

from mcp.action_runner import ACTION_TOOL_MAP, UnmappedActionError, make_action_runner
from mcp.fake import FakeGateway
from mcp.gateway import ToolGateway
from mcp.types import ToolBudgetExceeded, ToolCallOutcome, ToolSpec

__all__ = [
    "ToolGateway",
    "FakeGateway",
    "ToolSpec",
    "ToolCallOutcome",
    "ToolBudgetExceeded",
    "make_action_runner",
    "ACTION_TOOL_MAP",
    "UnmappedActionError",
]
