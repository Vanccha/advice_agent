"""Shared types for the tool gateway (contracts §3). Both `gateway.ToolGateway` and
`fake.FakeGateway` speak this exact vocabulary, so the modes/API layer can treat them
interchangeably.

Import as: ``from mcp_gateway.types import ToolSpec, ToolCallOutcome, ToolBudgetExceeded``.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# contracts §3: ToolResult envelope `source` values.
ToolSource = Literal[
    "diag_db", "core_api", "payment_api", "ticketing_api", "monitoring_api", "notification_api"
]

# contracts §3: the five adapters a tenant may configure.
AdapterName = Literal["core", "payment", "ticketing", "monitoring", "notification"]


class ToolSpec(BaseModel):
    """One tool's descriptor, as surfaced by `list_tools()`/`catalog()`."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    adapter: str
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ToolCallOutcome(BaseModel):
    """The result of exactly one `ToolGateway.call()` (contracts §3's `ToolResult`
    envelope, extended with gateway-level bookkeeping). A dead/unreachable adapter is
    reported here (`ok=False`, `error_code="ADAPTER_UNAVAILABLE"`) — `call()` itself never
    raises for that case."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    data: Any = None
    error_code: str | None = None
    error_message: str | None = None
    source: str | None = None
    duration_ms: float = 0.0
    adapter: str | None = None


class ToolBudgetExceeded(Exception):
    """Raised by `ToolGateway.call()`/`call_sync()` when a turn's `MAX_TOOL_CALLS_PER_TURN`
    budget is already spent — call N+1 raises instead of silently placing another call."""

    def __init__(self, tool_name: str, limit: int) -> None:
        super().__init__(
            f"tool call budget ({limit} calls/turn) already spent; refused to call {tool_name!r}"
        )
        self.tool_name = tool_name
        self.limit = limit
