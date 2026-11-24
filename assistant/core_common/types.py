"""Shared vocabulary (enums and data models) for the assistant's pure-logic core.

Where this sits: ``core_common``; imported by policy, recommendation, privacy, audit,
decision, modes, llm and api. It is pure data: no I/O, LLM or network dependency.

Key ideas:
- ``StrEnum`` values double as the strings stored in the DB and sent to the model, so there
  is exactly one spelling of each mode, intent and issue type.
- ``Decision[T]`` pairs every model answer with a confidence and rationale, so callers can
  apply a confidence floor instead of trusting the value blindly.
- Frozen models for results (cannot be mutated by accident); ``AdvisoryProfile`` is the one
  mutable exception because it fills in turn by turn.

Read next: ``decision/base.py``, ``policy/engine.py``.
See: docs/contracts.md Â§4.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

# --- Enums -------------------------------------------------------------------


class Mode(StrEnum):
    """States of the explicit state machine (contracts §4.3)."""

    ROUTER = "ROUTER"
    ADVISORY = "ADVISORY"
    DIAGNOSTIC = "DIAGNOSTIC"
    STATUS_QUERY = "STATUS_QUERY"
    ACTION = "ACTION"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    ESCALATED = "ESCALATED"
    CLOSING = "CLOSING"


class Intent(StrEnum):
    """Output of ``DecisionService.classify_intent`` (contracts §4.3 / §4.5)."""

    ADVISORY = "advisory"
    PROBLEM_REPORT = "problem_report"
    STATUS_QUERY = "status_query"
    SMALLTALK = "smalltalk"
    OUT_OF_SCOPE = "out_of_scope"


class Department(StrEnum):
    """Department codes (contracts §1.3, §2.6, `config/tenants/*/tenant.yaml`)."""

    TECHNICAL_INFRA = "TECHNICAL_INFRA"
    BILLING = "BILLING"
    SUBSCRIPTION_OPS = "SUBSCRIPTION_OPS"
    FIELD_INSTALL = "FIELD_INSTALL"


class Priority(StrEnum):
    """Ticket priority (contracts §1.3)."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class IssueType(StrEnum):
    """Structured ticket `issue_type` (contracts §4.1) — exactly the ten values."""

    STUCK_PROVISIONING = "stuck_provisioning"
    PAID_NOT_ACTIVE = "paid_not_active"
    REGIONAL_OUTAGE = "regional_outage"
    DOUBLE_CHARGE = "double_charge"
    MISSED_INSTALLATION = "missed_installation"
    PAYMENT_SYSTEM_DOWN = "payment_system_down"
    PLAN_CHANGE_REQUEST = "plan_change_request"
    REFUND_REQUEST = "refund_request"
    INFRASTRUCTURE_REPAIR = "infrastructure_repair"
    OTHER = "other"


class DiagnosisScope(StrEnum):
    """Scope of a `Diagnosis` (contracts §4.3)."""

    CUSTOMER_SPECIFIC = "customer_specific"
    REGIONAL_INCIDENT = "regional_incident"
    SYSTEM_WIDE = "system_wide"


class UsageType(StrEnum):
    """Advisory profile usage purposes (contracts §4.4)."""

    STUDENT = "student"
    FAMILY = "family"
    HOME_OFFICE = "home_office"
    GAMING = "gaming"
    STREAMING = "streaming"
    BASIC = "basic"


class CommitmentPreference(StrEnum):
    """Advisory profile commitment preference (contracts §4.4)."""

    NONE = "none"
    TWELVE = "12"
    TWENTY_FOUR = "24"
    ANY = "any"


class StepType(StrEnum):
    """`audit_entries.step_type` (contracts §1.5)."""

    MODE_DECISION = "mode_decision"
    DECISION_SERVICE = "decision_service"
    TOOL_CALL = "tool_call"
    POLICY_CHECK = "policy_check"
    ACTION = "action"
    TICKET_CREATED = "ticket_created"
    USER_NOTIFIED = "user_notified"
    ALERT_RECEIVED = "alert_received"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    ESCALATION = "escalation"


# --- Generic Decision[T] (contracts §4.5) ------------------------------------

T = TypeVar("T")


class Decision(BaseModel, Generic[T]):
    """Result of a single `DecisionService` call."""

    model_config = ConfigDict(frozen=True)

    value: T
    confidence: float
    rationale: str
    model: str
    raw: dict[str, Any] = Field(default_factory=dict)


class Triage(BaseModel):
    """Result of the combined `DecisionService.triage` call: the three escalation decisions
    (issue type, priority, department) made from one shared context in one round-trip."""

    model_config = ConfigDict(frozen=True)

    issue_type: Decision[IssueType]
    priority: Decision[Priority]
    department: Decision[Department]


# --- Advisory / recommendation (contracts §4.4) ------------------------------


class AdvisoryProfile(BaseModel):
    """Progressively filled during ADVISORY mode; NOT frozen — the state machine (owned by
    another agent) mutates/replaces it turn by turn as answers arrive."""

    model_config = ConfigDict(validate_assignment=True)

    usage: list[UsageType] = Field(default_factory=list)
    household_size: int | None = None
    device_count: int | None = None
    budget_gbp: float | None = None
    commitment_preference: CommitmentPreference | None = None
    needs_static_ip: bool = False
    needs_tv: bool = False


class PackageOffer(BaseModel):
    """One scored package recommendation (contracts §4.4)."""

    model_config = ConfigDict(frozen=True)

    package_code: str
    name: str
    down_mbps: int
    up_mbps: int
    commitment_months: int
    monthly_price_gbp: float
    score: float
    reasons: list[str]
    is_best: bool


# --- Diagnostic mode (contracts §4.3) ----------------------------------------


class Diagnosis(BaseModel):
    """What diagnostic mode concluded: root cause, scope, confidence and the evidence."""

    model_config = ConfigDict(frozen=True)

    root_cause: str
    scope: DiagnosisScope
    confidence: float
    evidence: dict[str, Any] = Field(default_factory=dict)
    affected_customers: list[str] = Field(default_factory=list)
    incident_no: str | None = None


# --- Policy engine (contracts §4.6) ------------------------------------------


class PolicyDecision(BaseModel):
    """Answer of `PolicyEngine.check`: allowed or not, whether confirmation is needed, and why.

    `reason_code` is for logic and audit, `reason_en` is the sentence a customer may read.
    """

    model_config = ConfigDict(frozen=True)

    allowed: bool
    requires_confirmation: bool = False
    reason_code: str | None = None
    reason_en: str | None = None
    escalate_to: Department | None = None
    limit_applied: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


# --- Audit (contracts §1.5, §4.8) --------------------------------------------


class AuditStep(BaseModel):
    """Everything needed to append one `audit_entries` row, before hash-chaining.

    `AuditLog.append()` (assistant/audit/log.py) takes these fields (plus the
    conversation/tenant identifiers) and computes `prev_hash`/`entry_hash`/`seq` itself.
    """

    model_config = ConfigDict(frozen=True)

    step_type: StepType
    conversation_id: str
    tenant: str
    actor: str
    summary: str
    reason: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    policy_decision: dict[str, Any] | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output_digest: str | None = None


# --- Small shared literal helper ---------------------------------------------

# The comparison operators a policy `conditions[]` entry may use.
ConditionOperator = Literal["eq", "in", "lt", "lte", "gt", "gte", "ne", "exists"]
