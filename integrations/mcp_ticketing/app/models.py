"""Pydantic input/output models for every `mcp-ticketing` tool (docs/contracts.md section 3 and 4.1).

Where this sits: imported by `tools.py`; the models are the source of each
tool's JSON schema and mirror the ticketing API's structured ticket payload.

Key ideas:
- A structured ticket (requester, evidence, attempted steps) lets the receiving
  department act without asking the customer again.
- Models are the contract; change docs/contracts.md first.

Table of contents: shared shapes (requester, evidence, comments, detail,
summary), create_structured_ticket, get_ticket, list_customer_tickets,
find_tickets_by_incident, add_ticket_comment.

Read next: `tools.py`.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Department = Literal["TECHNICAL_INFRA", "BILLING", "SUBSCRIPTION_OPS", "FIELD_INSTALL"]
IssueType = Literal[
    "stuck_provisioning",
    "paid_not_active",
    "regional_outage",
    "double_charge",
    "missed_installation",
    "payment_system_down",
    "plan_change_request",
    "refund_request",
    "infrastructure_repair",
    "other",
]
Priority = Literal["LOW", "NORMAL", "HIGH", "URGENT"]
TicketSource = Literal["web", "phone", "api", "monitoring"]
AuthorType = Literal["agent", "api_client", "system", "customer"]


# --- shared nested shapes (§4.1) ------------------------------------------


class RequesterInput(BaseModel):
    customer_no: Optional[str] = None
    name: Optional[str] = None
    contact: Optional[str] = None


class EvidenceInput(BaseModel):
    record_ids: dict[str, Any] = Field(default_factory=dict)
    error_codes: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    queried_sources: list[str] = Field(default_factory=list)


class AttemptedStepInput(BaseModel):
    step: str
    result: str
    outcome: str


class TicketComment(BaseModel):
    id: int
    author: str
    author_type: str
    body: str
    is_internal: bool
    created_at: str


class StatusHistoryEntry(BaseModel):
    id: int
    from_status: Optional[str] = None
    to_status: str
    actor: str
    note: Optional[str] = None
    created_at: str


class TicketDetail(BaseModel):
    """Mirrors the ticketing service's `TicketResponse` field-for-field."""

    id: int
    ticket_key: str
    department: str
    status: str
    priority: str
    issue_type: str
    subject: str
    body: str
    requester_customer_no: Optional[str] = None
    requester_name: Optional[str] = None
    requester_contact: Optional[str] = None
    source: str
    external_ref: Optional[str] = None
    incident_ref: Optional[str] = None
    evidence: Optional[dict[str, Any]] = None
    attempted_steps: Optional[list[dict[str, Any]]] = None
    suggested_next_step: Optional[str] = None
    affected_customers: Optional[list[str]] = None
    urgency_reason: Optional[str] = None
    assignee: Optional[str] = None
    sla_due_at: str
    created_at: str
    updated_at: str
    resolved_at: Optional[str] = None
    comments: list[TicketComment] = Field(default_factory=list)
    status_history: list[StatusHistoryEntry] = Field(default_factory=list)


class TicketSummary(BaseModel):
    """Mirrors `TicketListItem` — the shape `GET /api/v1/tickets` returns."""

    id: int
    ticket_key: str
    department: str
    status: str
    priority: str
    issue_type: str
    subject: str
    requester_customer_no: Optional[str] = None
    assignee: Optional[str] = None
    sla_due_at: str
    created_at: str
    updated_at: str
    resolved_at: Optional[str] = None


# --- create_structured_ticket ---------------------------------------------


class CreateStructuredTicketInput(BaseModel):
    department: Department
    issue_type: IssueType
    priority: Priority
    subject: str
    body: str
    source: TicketSource = "api"
    external_ref: Optional[str] = Field(
        default=None, description="idempotency/correlation key; a repeat returns the existing ticket"
    )
    incident_ref: Optional[str] = Field(
        default=None, description="INC-... number, links this ticket to a regional outage"
    )
    requester: Optional[RequesterInput] = None
    evidence: Optional[EvidenceInput] = None
    attempted_steps: Optional[list[AttemptedStepInput]] = None
    affected_customers: Optional[list[str]] = None
    suggested_next_step: Optional[str] = None
    urgency_reason: Optional[str] = None


class CreateStructuredTicketOutput(BaseModel):
    ticket: TicketDetail
    already_existed: bool = Field(
        description="true when `external_ref` already had a ticket and the company API "
        "returned it instead of creating a new one (idempotent 200, vs. a fresh 201)"
    )


# --- get_ticket -----------------------------------------------------------


class GetTicketInput(BaseModel):
    ticket_key: str


class GetTicketOutput(BaseModel):
    ticket: TicketDetail


# --- list_customer_tickets ------------------------------------------------


class ListCustomerTicketsInput(BaseModel):
    customer_no: str
    status: Optional[str] = Field(
        default=None, description="NEW|TRIAGE|IN_PROGRESS|WAITING_CUSTOMER|RESOLVED|CLOSED|REJECTED"
    )
    limit: int = 50
    offset: int = 0


class ListCustomerTicketsOutput(BaseModel):
    tickets: list[TicketSummary]
    total: int


# --- find_tickets_by_incident ---------------------------------------------


class FindTicketsByIncidentInput(BaseModel):
    incident_no: str = Field(description="e.g. INC-2026-001; mapped to ?incident_ref=")
    limit: int = 50
    offset: int = 0


class FindTicketsByIncidentOutput(BaseModel):
    tickets: list[TicketSummary]
    total: int


# --- add_ticket_comment ---------------------------------------------------


class AddTicketCommentInput(BaseModel):
    ticket_key: str
    body: str
    author: str = "netswift-mcp-ticketing"
    is_internal: bool = False


class AddTicketCommentOutput(BaseModel):
    comment: TicketComment
