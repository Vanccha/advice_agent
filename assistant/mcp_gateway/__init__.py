"""The tool gateway: the only way the assistant touches the company's systems.

Public entry points: ``ToolGateway`` (live, over streamable-HTTP MCP adapters), ``FakeGateway``
(same surface, canned answers, for tests and evals), ``make_action_runner`` (connects
``policy.executor.ActionExecutor`` to a gateway) and the shared types ``ToolSpec``,
``ToolCallOutcome``, ``ToolBudgetExceeded``.

The package is named ``mcp_gateway``, not ``mcp``: a package called ``mcp`` would shadow the
installed MCP client SDK, so ``import mcp`` inside it would no longer reach the real SDK.
See: docs/contracts.md §3.
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
