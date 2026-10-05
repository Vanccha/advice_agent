from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Department = Literal["TECHNICAL_INFRA", "BILLING", "SUBSCRIPTION_OPS", "FIELD_INSTALL"]
Priority = Literal["LOW", "NORMAL", "HIGH", "URGENT"]
Status = Literal["NEW", "TRIAGE", "IN_PROGRESS", "WAITING_CUSTOMER", "RESOLVED", "CLOSED", "REJECTED"]
Source = Literal["web", "phone", "api", "monitoring"]
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
AuthorType = Literal["agent", "api_client", "system", "customer"]


class Requester(BaseModel):
    customer_no: str | None = None
    name: str | None = None
    contact: str | None = None


class TicketCreateRequest(BaseModel):
    """Structured ticket payload, docs/contracts.md §4.1."""

    department: Department
    issue_type: IssueType
    priority: Priority
    subject: str
    body: str
    source: Source
    external_ref: str | None = None
    incident_ref: str | None = None
    requester: Requester | None = None
    evidence: dict[str, Any] | None = None
    attempted_steps: list[dict[str, Any]] | None = None
    affected_customers: list[str] | None = None
    suggested_next_step: str | None = None
    urgency_reason: str | None = None


class TicketPatchRequest(BaseModel):
    status: Status | None = None
    assignee: str | None = None
    department: Department | None = None
    priority: Priority | None = None
    note: str | None = None


class CommentCreateRequest(BaseModel):
    author: str
    author_type: AuthorType
    body: str
    is_internal: bool = False


class CommentResponse(BaseModel):
    id: int
    author: str
    author_type: str
    body: str
    is_internal: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class StatusHistoryResponse(BaseModel):
    id: int
    from_status: str | None
    to_status: str
    actor: str
    note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketResponse(BaseModel):
    id: int
    ticket_key: str
    department: str
    status: str
    priority: str
    issue_type: str
    subject: str
    body: str
    requester_customer_no: str | None
    requester_name: str | None
    requester_contact: str | None
    source: str
    external_ref: str | None
    incident_ref: str | None
    evidence: dict[str, Any] | None
    attempted_steps: list[dict[str, Any]] | None
    suggested_next_step: str | None
    affected_customers: list[str] | None
    urgency_reason: str | None
    assignee: str | None
    sla_due_at: datetime
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    comments: list[CommentResponse] = Field(default_factory=list)
    status_history: list[StatusHistoryResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class TicketListItem(BaseModel):
    id: int
    ticket_key: str
    department: str
    status: str
    priority: str
    issue_type: str
    subject: str
    requester_customer_no: str | None
    assignee: str | None
    sla_due_at: datetime
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None

    model_config = {"from_attributes": True}


class TicketListResponse(BaseModel):
    items: list[TicketListItem]
    total: int


class DepartmentResponse(BaseModel):
    code: str
    display_name: str
    email: str | None
    channel_slug: str

    model_config = {"from_attributes": True}


class WebhookSubscriptionCreateRequest(BaseModel):
    name: str | None = None
    target_url: str
    events: list[str] = Field(default_factory=list)
    secret: str | None = None
    is_active: bool = True


class WebhookSubscriptionResponse(BaseModel):
    id: int
    name: str | None
    target_url: str
    events: list[str]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
