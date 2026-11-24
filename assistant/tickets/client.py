"""``TicketService``: the assistant-side client for the ticketing adapter's MCP tools.

Where this sits: ``tickets`` package. ``modes`` call it to create and look up tickets. It
works over any gateway with ``call_sync`` (``mcp_gateway.ToolGateway`` in production,
``mcp_gateway.FakeGateway`` in tests and evals) and never talks to the ticketing system directly.

Key ideas:
- Gateway as dependency: the client only needs the small ``_CallableGateway`` protocol, so it
  is tested without a network.
- Idempotent create: a repeat ``external_ref`` returns the existing ticket with
  ``TicketRef.created=False``; that is a normal result, not an error.
- Tolerant of envelopes: adapter answers arrive wrapped (ToolResult, then a named record), and
  flat dicts from fakes are accepted too.

Read next: ``tickets/builder.py``, ``mcp_gateway/types.py``.
See: docs/contracts.md §3, §2.5.
"""
from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from mcp_gateway.types import ToolCallOutcome
from tickets.builder import StructuredTicket


class _CallableGateway(Protocol):
    """The one gateway method this client needs."""

    def call_sync(self, tool_name: str, arguments: dict[str, Any]) -> ToolCallOutcome: ...


class TicketRef(BaseModel):
    """What `create()` hands back: enough to track the ticket without re-fetching it."""

    model_config = ConfigDict(frozen=True)

    ticket_key: str
    department: str
    status: str
    priority: str | None = None
    # False when `create()` returned an existing ticket (idempotent replay by external_ref).
    created: bool = True


class TicketServiceError(RuntimeError):
    """The ticketing adapter is unreachable or returned an unexpected shape.

    Not raised for "ticket already existed": that is the normal idempotent `create()` path
    (`TicketRef.created=False`).
    """

    def __init__(self, tool_name: str, outcome: ToolCallOutcome) -> None:
        super().__init__(
            f"ticketing adapter call '{tool_name}' failed: "
            f"{outcome.error_code}: {outcome.error_message}"
        )
        self.tool_name = tool_name
        self.outcome = outcome


# A tool answer arrives wrapped twice: the gateway hands back `ToolCallOutcome.data`, which
# holds the adapter's own `{ok, data, error, source}` envelope (contracts §3), which in turn
# holds a named record or collection (`{"ticket": {...}, "already_existed": true}`,
# `{"tickets": [...], "total": N}`). Fakes and fixtures pass flat dicts instead, so every
# peel below is optional and order-independent.
_COLLECTION_KEYS = ("tickets", "items")


def _payload(data: Any) -> Any:
    """Peel the adapter's ToolResult envelope if it is there."""
    if isinstance(data, dict) and "ok" in data and "data" in data:
        return data.get("data")
    return data


def _record(data: Any, key: str = "ticket") -> dict[str, Any]:
    """The single ticket record, however deeply the adapter wrapped it."""
    payload = _payload(data)
    if isinstance(payload, dict):
        inner = payload.get(key)
        if isinstance(inner, dict):
            return inner
        return payload
    return {}


def _as_items(data: Any) -> list[dict[str, Any]]:
    """The list of ticket records, whichever collection key the adapter used."""
    payload = _payload(data)
    if isinstance(payload, dict):
        for key in _COLLECTION_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return list(value)
        return []
    if isinstance(payload, list):
        return list(payload)
    return []


class TicketService:
    """Create and look up tickets through the ticketing adapter."""

    def __init__(self, gateway: _CallableGateway) -> None:
        self._gateway = gateway

    def create(self, ticket: StructuredTicket) -> TicketRef:
        """Create the ticket, or return the existing one for the same `external_ref`."""
        outcome = self._gateway.call_sync(
            "create_structured_ticket", ticket.model_dump(mode="json")
        )
        if not outcome.ok:
            raise TicketServiceError("create_structured_ticket", outcome)
        envelope = _payload(outcome.data)
        record = _record(outcome.data)
        if "ticket_key" not in record:
            raise TicketServiceError("create_structured_ticket", outcome)
        # The adapter reports an idempotent replay as `already_existed`; a flat fixture may
        # say `created`. Either way `created` means "this call made the ticket".
        already_existed = (
            envelope.get("already_existed") if isinstance(envelope, dict) else None
        )
        if already_existed is None:
            created = bool(record.get("created", envelope.get("created", True)
                                      if isinstance(envelope, dict) else True))
        else:
            created = not bool(already_existed)
        return TicketRef(
            ticket_key=record["ticket_key"],
            department=record.get("department", ticket.department.value),
            status=record.get("status", "NEW"),
            priority=record.get("priority", ticket.priority.value),
            created=created,
        )

    def get(self, ticket_key: str) -> dict[str, Any]:
        """One ticket record by key."""
        outcome = self._gateway.call_sync("get_ticket", {"ticket_key": ticket_key})
        if not outcome.ok:
            raise TicketServiceError("get_ticket", outcome)
        return _record(outcome.data)

    def list_for_customer(self, customer_no: str) -> list[dict[str, Any]]:
        """All tickets of one customer (used by status queries)."""
        outcome = self._gateway.call_sync(
            "list_customer_tickets", {"customer_no": customer_no}
        )
        if not outcome.ok:
            raise TicketServiceError("list_customer_tickets", outcome)
        return _as_items(outcome.data)

    def find_for_incident(self, incident_no: str) -> list[dict[str, Any]]:
        """Tickets already attached to an incident.

        Lets the assistant avoid opening a second ticket for a known regional outage: every
        affected customer attaches to the one incident ticket (contracts §4.3).
        """
        outcome = self._gateway.call_sync(
            "find_tickets_by_incident", {"incident_no": incident_no}
        )
        if not outcome.ok:
            raise TicketServiceError("find_tickets_by_incident", outcome)
        return _as_items(outcome.data)
