"""The `ToolResult` envelope every MCP tool returns (docs/contracts.md §3).

Every tool in this integration layer returns this shape — `{ok, data, error,
source}` — and never lets a raw exception escape to the MCP transport. `source`
tells the caller which system actually answered the question, which matters
because the whole point of this layer is "reads via diag_db, writes via the
company's own REST API, never SQL writes."
"""
from __future__ import annotations

from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, Field

Source = Literal[
    "diag_db",
    "core_api",
    "payment_api",
    "ticketing_api",
    "monitoring_api",
    "notification_api",
]

T = TypeVar("T")


class ToolError(BaseModel):
    """The error half of the envelope. `code` is preserved verbatim from the
    company's own error envelope when the failure came from a REST call (e.g.
    `SCOPE_DENIED`, `ILLEGAL_TRANSITION`, `CREDIT_LIMIT_EXCEEDED`,
    `RESEND_COOLDOWN`, `UPSTREAM_UNAVAILABLE`), or an adapter-local code
    (`INVALID_INPUT`, `NOT_FOUND`, `READ_ONLY_VIOLATION`, ...) otherwise.
    """

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel, Generic[T]):
    """`{ok, data, error, source}` — see docs/contracts.md §3.

    Tool handlers should not construct this directly; use `ok(...)` / `fail(...)`
    below so every tool produces the envelope the same way.
    """

    ok: bool
    data: Optional[T] = None
    error: Optional[ToolError] = None
    source: Optional[Source] = None


def ok(data: Any, source: Source) -> ToolResult[Any]:
    """Build a successful envelope. `data` is normally a Pydantic model instance
    (the tool's declared output model) but may be a plain dict/list for
    pass-through cases.
    """
    return ToolResult(ok=True, data=data, error=None, source=source)


def fail(code: str, message: str, source: Optional[Source] = None, **details: Any) -> ToolResult[Any]:
    """Build a failed envelope. `**details` becomes `error.details`."""
    return ToolResult(ok=False, data=None, error=ToolError(code=code, message=message, details=details), source=source)
