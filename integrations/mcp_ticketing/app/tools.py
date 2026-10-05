"""`mcp-ticketing` tools (docs/contracts.md §3)."""
from __future__ import annotations

from typing import Any

import httpx

from common.result import ToolResult, fail, ok
from common.tool_spec import register_tool

from .deps import ticketing_api_client
from .models import (
    AddTicketCommentInput,
    AddTicketCommentOutput,
    CreateStructuredTicketInput,
    CreateStructuredTicketOutput,
    FindTicketsByIncidentInput,
    FindTicketsByIncidentOutput,
    GetTicketInput,
    GetTicketOutput,
    ListCustomerTicketsInput,
    ListCustomerTicketsOutput,
    TicketComment,
    TicketDetail,
    TicketSummary,
)

SOURCE_TICKETING = "ticketing_api"

# Only transport-level failures are retried by CompanyApiClient.request_raw;
# an HTTP response that came back (even an error one) is a real answer.
_RETRYABLE_EXCEPTIONS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout)


def _build_ticket_payload(inp: CreateStructuredTicketInput) -> dict[str, Any]:
    """Build the §4.1 payload for `POST /api/v1/tickets`. Every field the
    caller actually populated is forwarded — nothing optional is dropped just
    because it's empty/None was not explicitly intended; only fields the
    caller left unset are omitted so the company API applies its own defaults.
    """
    payload: dict[str, Any] = {
        "department": inp.department,
        "issue_type": inp.issue_type,
        "priority": inp.priority,
        "subject": inp.subject,
        "body": inp.body,
        "source": inp.source,
    }
    if inp.external_ref is not None:
        payload["external_ref"] = inp.external_ref
    if inp.incident_ref is not None:
        payload["incident_ref"] = inp.incident_ref
    if inp.requester is not None:
        payload["requester"] = inp.requester.model_dump()
    if inp.evidence is not None:
        payload["evidence"] = inp.evidence.model_dump()
    if inp.attempted_steps is not None:
        payload["attempted_steps"] = [step.model_dump() for step in inp.attempted_steps]
    if inp.affected_customers is not None:
        payload["affected_customers"] = inp.affected_customers
    if inp.suggested_next_step is not None:
        payload["suggested_next_step"] = inp.suggested_next_step
    if inp.urgency_reason is not None:
        payload["urgency_reason"] = inp.urgency_reason
    return payload


async def _post_ticket(payload: dict[str, Any]) -> tuple[ToolResult[Any], bool]:
    """POST /api/v1/tickets and report whether the ticket already existed.

    The ticketing service is idempotent on `external_ref`: a brand-new
    ticket comes back `201 Created`, a repeat of a known `external_ref`
    comes back `200 OK` with the *existing* ticket. `CompanyApiClient.call`
    collapses both into the same `ok(...)` envelope, so the raw response is
    inspected here (via `request_raw`, which applies the same connection
    retry policy) instead, to recover that distinction faithfully.
    """
    client = ticketing_api_client()
    try:
        response = await client.request_raw("POST", "/api/v1/tickets", json=payload)
    except _RETRYABLE_EXCEPTIONS as exc:
        return fail("UPSTREAM_UNAVAILABLE", f"ticketing API unreachable: {exc!s}", SOURCE_TICKETING), False
    except httpx.HTTPError as exc:
        return fail("UPSTREAM_UNAVAILABLE", f"request to ticketing API failed: {exc!s}", SOURCE_TICKETING), False

    if 200 <= response.status_code < 300:
        body = response.json() if response.content else None
        return ok(body, SOURCE_TICKETING), response.status_code == 200

    code = f"HTTP_{response.status_code}"
    message = f"HTTP {response.status_code}"
    details: dict[str, Any] = {}
    try:
        error_body = response.json()
    except ValueError:
        error_body = None
    if isinstance(error_body, dict) and isinstance(error_body.get("error"), dict):
        err = error_body["error"]
        code = str(err.get("code", code))
        message = str(err.get("message", message))
        details = dict(err.get("details") or {})
    elif error_body is not None:
        message = str(error_body)[:500]
    details.setdefault("http_status", response.status_code)
    return fail(code, message, SOURCE_TICKETING, **details), False


async def handle_create_structured_ticket(inp: CreateStructuredTicketInput) -> ToolResult[Any]:
    payload = _build_ticket_payload(inp)
    result, already_existed = await _post_ticket(payload)
    if not result.ok:
        return result
    ticket = TicketDetail(**result.data)
    return ok(CreateStructuredTicketOutput(ticket=ticket, already_existed=already_existed), SOURCE_TICKETING)


async def handle_get_ticket(inp: GetTicketInput) -> ToolResult[Any]:
    result = await ticketing_api_client().get(f"/api/v1/tickets/{inp.ticket_key}", SOURCE_TICKETING)
    if not result.ok:
        return result
    return ok(GetTicketOutput(ticket=TicketDetail(**result.data)), SOURCE_TICKETING)


async def handle_list_customer_tickets(inp: ListCustomerTicketsInput) -> ToolResult[Any]:
    params: dict[str, Any] = {
        "customer_no": inp.customer_no,
        "limit": inp.limit,
        "offset": inp.offset,
    }
    if inp.status is not None:
        params["status"] = inp.status
    result = await ticketing_api_client().get("/api/v1/tickets", SOURCE_TICKETING, params=params)
    if not result.ok:
        return result
    body = result.data or {}
    tickets = [TicketSummary(**item) for item in body.get("items", [])]
    return ok(ListCustomerTicketsOutput(tickets=tickets, total=body.get("total", len(tickets))), SOURCE_TICKETING)


async def handle_find_tickets_by_incident(inp: FindTicketsByIncidentInput) -> ToolResult[Any]:
    params = {"incident_ref": inp.incident_no, "limit": inp.limit, "offset": inp.offset}
    result = await ticketing_api_client().get("/api/v1/tickets", SOURCE_TICKETING, params=params)
    if not result.ok:
        return result
    body = result.data or {}
    tickets = [TicketSummary(**item) for item in body.get("items", [])]
    return ok(FindTicketsByIncidentOutput(tickets=tickets, total=body.get("total", len(tickets))), SOURCE_TICKETING)


async def handle_add_ticket_comment(inp: AddTicketCommentInput) -> ToolResult[Any]:
    payload = {
        "author": inp.author,
        "author_type": "api_client",
        "body": inp.body,
        "is_internal": inp.is_internal,
    }
    result = await ticketing_api_client().post(
        f"/api/v1/tickets/{inp.ticket_key}/comments", SOURCE_TICKETING, json=payload
    )
    if not result.ok:
        return result
    return ok(AddTicketCommentOutput(comment=TicketComment(**result.data)), SOURCE_TICKETING)


def register_tools(mcp: Any) -> None:
    register_tool(
        mcp,
        name="create_structured_ticket",
        description="Create a structured department ticket; idempotent on external_ref.",
        input_model=CreateStructuredTicketInput,
        output_model=CreateStructuredTicketOutput,
        handler=handle_create_structured_ticket,
    )
    register_tool(
        mcp,
        name="get_ticket",
        description="Get a ticket by ticket_key, including its comments and status history.",
        input_model=GetTicketInput,
        output_model=GetTicketOutput,
        handler=handle_get_ticket,
    )
    register_tool(
        mcp,
        name="list_customer_tickets",
        description="List a customer's tickets, optionally filtered by status.",
        input_model=ListCustomerTicketsInput,
        output_model=ListCustomerTicketsOutput,
        handler=handle_list_customer_tickets,
    )
    register_tool(
        mcp,
        name="find_tickets_by_incident",
        description="Find tickets already linked to a network incident, to avoid opening duplicates.",
        input_model=FindTicketsByIncidentInput,
        output_model=FindTicketsByIncidentOutput,
        handler=handle_find_tickets_by_incident,
    )
    register_tool(
        mcp,
        name="add_ticket_comment",
        description="Add a comment to an existing ticket.",
        input_model=AddTicketCommentInput,
        output_model=AddTicketCommentOutput,
        handler=handle_add_ticket_comment,
    )
