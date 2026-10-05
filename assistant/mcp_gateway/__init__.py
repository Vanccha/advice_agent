"""The tool gateway: the only way the product touches the company (contracts §3).

Public surface:
    `ToolGateway` — live gateway over streamable-HTTP MCP adapters (`gateway.py`).
    `FakeGateway` — same surface, canned responses, no network (`fake.py`).
    `ToolSpec`, `ToolCallOutcome`, `ToolBudgetExceeded` — shared types (`types.py`).
    `make_action_runner` — wires `policy.executor.ActionExecutor` to a gateway
        (`action_runner.py`).

Note for maintainers: this package is importable as top-level `mcp`, which collides with
the pip-installed `mcp` client SDK: this package is named `mcp_gateway`, so `import mcp`
inside it resolves to the real SDK.
"""
from __future__ import annotations

from mcp_gateway.action_runner import ACTION_TOOL_MAP, UnmappedActionError, make_action_runner
from mcp_gateway.fake import FakeGateway
from mcp_gateway.gateway import ToolGateway
from mcp_gateway.types import ToolBudgetExceeded, ToolCallOutcome, ToolSpec

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
