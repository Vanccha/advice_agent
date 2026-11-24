"""The `ToolResult` envelope every MCP tool returns (docs/contracts.md section 3).

Where this sits: built by every tool handler on every adapter and consumed by
the assistant's MCP gateway; `source` tells the consumer which company system
actually answered.

Key ideas:
- One shape for success and failure, `{ok, data, error, source}`, so callers
  never parse ad-hoc payloads and never see a raw exception from the MCP
  transport. Failures are values, not exceptions.
- Error codes are preserved from the company when the failure came from its
  REST API (`SCOPE_DENIED`, `ILLEGAL_TRANSITION`, ...) and adapter-local
  otherwise (`INVALID_INPUT`, `NOT_FOUND`, ...).
- `source` is explicit provenance: reads via `diag_db`, writes via a company
  `*_api`.

Read next: `http.py` (maps REST errors into this envelope) and `tool_spec.py`.
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
    """The error half of the envelope.

    `code` is preserved verbatim from the company's own error envelope when the
    failure came from a REST call (`SCOPE_DENIED`, `ILLEGAL_TRANSITION`,
    `CREDIT_LIMIT_EXCEEDED`, `RESEND_COOLDOWN`, `UPSTREAM_UNAVAILABLE`), and is an
    adapter-local code (`INVALID_INPUT`, `NOT_FOUND`, `READ_ONLY_VIOLATION`, ...)
    otherwise. Keeping the company's code means the consumer can react to it
    without the adapter translating (and possibly distorting) it.
    """

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel, Generic[T]):
    """`{ok, data, error, source}`: see docs/contracts.md section 3.

    Handlers should not construct this directly; use `ok(...)` / `fail(...)` so
    every tool builds the envelope the same way.
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
