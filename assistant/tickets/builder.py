"""The structured ticket builder: one complete, masked, idempotent ticket payload.

Where this sits: ``tickets`` package. ``modes.action`` / ``modes.escalation`` call
``build_structured_ticket``; ``tickets.client.TicketService`` then sends the result to the
ticketing adapter. Masking comes from ``tickets.masking_rules``.

Key ideas:
- Write for the receiving department: the body says what was checked, found and tried, so
  nobody asks the customer the same questions again.
- Idempotency key: ``external_ref = <conversation_id>:<issue_type>`` means a repeat build
  maps to the same ticket instead of a duplicate.
- A ticket is a PII boundary: masking happens while the payload is built, so a raw name or
  phone number cannot survive into a ``StructuredTicket``.

Read next: ``tickets/masking_rules.py``, ``tickets/client.py``.
See: docs/contracts.md §4.1, §4.7.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from core_common.types import Department, IssueType, Priority
from tickets.masking_rules import mask_ticket_payload


class TicketRequester(BaseModel):
    """Who the ticket is for. `name` and `contact` are masked; `customer_no` is not."""

    model_config = ConfigDict(frozen=True)

    customer_no: str
    name: str
    contact: str


class TicketEvidence(BaseModel):
    """Facts the assistant gathered: record ids, error codes, observations, sources queried."""

    model_config = ConfigDict(frozen=True)

    record_ids: dict[str, Any] = Field(default_factory=dict)
    error_codes: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    queried_sources: list[str] = Field(default_factory=list)


class AttemptedStep(BaseModel):
    """One thing the assistant already tried, so the department does not repeat it."""

    model_config = ConfigDict(frozen=True)

    step: str
    result: str
    outcome: str  # e.g. "info", "success", "failed", "blocked" (a policy denial)


class StructuredTicket(BaseModel):
    """Exactly the payload of contracts §4.1 (assistant -> `POST /api/v1/tickets`)."""

    model_config = ConfigDict(frozen=True)

    department: Department
    issue_type: IssueType
    priority: Priority
    subject: str
    body: str
    source: Literal["web", "phone", "api", "monitoring"] = "api"
    external_ref: str
    incident_ref: str | None = None
    requester: TicketRequester
    evidence: TicketEvidence
    attempted_steps: list[AttemptedStep] = Field(default_factory=list)
    affected_customers: list[str] = Field(default_factory=list)
    suggested_next_step: str
    urgency_reason: str


def build_external_ref(conversation_id: str, issue_type: IssueType | str) -> str:
    """`<conversation_id>:<issue_type>` (contracts §4.1): the idempotency key.

    The same issue in the same conversation always yields the same `external_ref`, and the
    ticketing API answers a repeat with the existing ticket (HTTP 200, not 201) instead of
    opening a duplicate.
    """
    issue = issue_type.value if isinstance(issue_type, IssueType) else str(issue_type)
    return f"{conversation_id}:{issue}"


def build_structured_ticket(
    *,
    conversation_id: str,
    department: Department | str,
    issue_type: IssueType | str,
    priority: Priority | str,
    subject_en: str,
    body_en: str,
    requester_customer_no: str,
    requester_name: str,
    requester_contact: str,
    suggested_next_step_en: str,
    urgency_reason_en: str,
    evidence_record_ids: dict[str, Any] | None = None,
    evidence_error_codes: list[str] | None = None,
    evidence_observations: list[str] | None = None,
    evidence_queried_sources: list[str] | None = None,
    attempted_steps: list[dict[str, str] | AttemptedStep] | None = None,
    affected_customers: list[str] | None = None,
    incident_ref: str | None = None,
    source: Literal["web", "phone", "api", "monitoring"] = "api",
    allow_unmasked: frozenset[str] | set[str] | None = None,
) -> StructuredTicket:
    """Build one `StructuredTicket`, masked at construction time.

    A ticket is a UK GDPR boundary just like a model call or a trace (contracts §4.7): raw PII
    in `requester_name`/`requester_contact`, or incidentally in any free-text field, never
    survives into the result. Fields named in `allow_unmasked` (normally the tenant's
    `policy.yaml: pii.allow_unmasked`: customer_no, ticket_key, ...) stay intact, since a
    masked customer number would be useless to the receiving department.

    `body_en` must be a plain-English narrative for that department: what was checked, what
    was found, what was tried and why the assistant could not finish (including any
    policy-blocked step).

    Building twice with the same `conversation_id` and `issue_type` gives the same
    `external_ref`, which is what makes ticket creation idempotent.
    """
    issue_type_enum = issue_type if isinstance(issue_type, IssueType) else IssueType(issue_type)

    raw_steps = [
        step if isinstance(step, dict) else step.model_dump(mode="json")
        for step in (attempted_steps or [])
    ]

    raw_payload: dict[str, Any] = {
        "department": Department(department).value,
        "issue_type": issue_type_enum.value,
        "priority": Priority(priority).value,
        "subject": subject_en,
        "body": body_en,
        "source": source,
        "external_ref": build_external_ref(conversation_id, issue_type_enum),
        "incident_ref": incident_ref,
        "requester": {
            "customer_no": requester_customer_no,
            "name": requester_name,
            "contact": requester_contact,
        },
        "evidence": {
            "record_ids": evidence_record_ids or {},
            "error_codes": evidence_error_codes or [],
            "observations": evidence_observations or [],
            "queried_sources": evidence_queried_sources or [],
        },
        "attempted_steps": raw_steps,
        "affected_customers": affected_customers or [requester_customer_no],
        "suggested_next_step": suggested_next_step_en,
        "urgency_reason": urgency_reason_en,
    }

    masked_payload = mask_ticket_payload(
        raw_payload, allow_unmasked=allow_unmasked, extra_names=[requester_name]
    )
    return StructuredTicket.model_validate(masked_payload)
