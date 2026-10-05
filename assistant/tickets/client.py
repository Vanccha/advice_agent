"""`TicketService`: the assistant-side client for the ticketing adapter's MCP tools
(contracts §3 `mcp-ticketing`), built on whatever gateway the caller wires in —
`mcp.gateway.ToolGateway` in production, `mcp.fake.FakeGateway` in tests/evals.

Import as: ``from tickets.client import TicketService, TicketRef, TicketServiceError``.
"""
from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from mcp.types import ToolCallOutcome
from tickets.builder import StructuredTicket


class _CallableGateway(Protocol):
    def call_sync(self, tool_name: str, arguments: dict[str, Any]) -> ToolCallOutcome: ...


class TicketRef(BaseModel):
    """What `create()` hands back: enough to track the ticket without re-fetching it."""

    model_config = ConfigDict(frozen=True)

    ticket_key: str
    department: str
    status: str
    priority: str | None = None
    # False when `create()` returned an *existing* ticket — the external_ref-based
    # idempotent replay (contracts §2.5: "200, not 201, when external_ref already exists").
    created: bool = True


class TicketServiceError(RuntimeError):
    """Raised when the ticketing adapter is unreachable or returns an unexpected shape.
    Never raised for "ticket already existed" — that is the normal idempotent `create()`
    path (`TicketRef.created=False`), not an error."""

    def __init__(self, tool_name: str, outcome: ToolCallOutcome) -> None:
        super().__init__(
            f"ticketing adapter call '{tool_name}' failed: "
            f"{outcome.error_code}: {outcome.error_message}"
        )
        self.tool_name = tool_name
        self.outcome = outcome


def _as_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        return list(data.get("items", []))
    if isinstance(data, list):
        return list(data)
    return []


class TicketService:
    def __init__(self, gateway: _CallableGateway) -> None:
        self._gateway = gateway

    def create(self, ticket: StructuredTicket) -> TicketRef:
        outcome = self._gateway.call_sync(
            "create_structured_ticket", ticket.model_dump(mode="json")
        )
        if not outcome.ok:
            raise TicketServiceError("create_structured_ticket", outcome)
        data = outcome.data or {}
        return TicketRef(
            ticket_key=data["ticket_key"],
            department=data.get("department", ticket.department.value),
            status=data.get("status", "NEW"),
            priority=data.get("priority", ticket.priority.value),
            created=bool(data.get("created", True)),
        )

    def get(self, ticket_key: str) -> dict[str, Any]:
        outcome = self._gateway.call_sync("get_ticket", {"ticket_key": ticket_key})
        if not outcome.ok:
            raise TicketServiceError("get_ticket", outcome)
        return outcome.data or {}

    def list_for_customer(self, customer_no: str) -> list[dict[str, Any]]:
        outcome = self._gateway.call_sync(
            "list_customer_tickets", {"customer_no": customer_no}
        )
        if not outcome.ok:
            raise TicketServiceError("list_customer_tickets", outcome)
        return _as_items(outcome.data)

    def find_for_incident(self, incident_no: str) -> list[dict[str, Any]]:
        """Used to avoid opening a second ticket for a known regional outage (contracts
        §4.3/§5 scenario c: every customer in an affected region attaches to the one
        existing incident ticket, never a new per-customer ticket)."""
        outcome = self._gateway.call_sync(
            "find_tickets_by_incident", {"incident_no": incident_no}
        )
        if not outcome.ok:
            raise TicketServiceError("find_tickets_by_incident", outcome)
        return _as_items(outcome.data)
