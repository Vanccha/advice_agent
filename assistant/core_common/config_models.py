"""Pydantic models describing one tenant's YAML configuration.

Where this sits: pure data layer under ``core_common``. ``core_common.config`` (the loader)
validates ``tenant.yaml``, ``policy.yaml`` and ``routing.yaml`` into these models; policy,
recommendation, privacy, decision and modes then read them.

Key ideas:
- Frozen models: configuration is immutable once loaded, so it is safe to cache and share.
- Validate at the edge: a bad YAML file fails at load time (``TenantConfig`` also checks
  that the model can only be offered read-only tools), not in the middle of a customer turn.
- One model per YAML file (``TenantFile``, ``PolicyFile``, ``RoutingFile``), joined in
  ``TenantConfig``.

Read next: ``core_common/config.py`` (the loader), ``policy/engine.py`` (consumes ``PolicyFile``).
See: docs/contracts.md §2.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --- tenant.yaml -------------------------------------------------------------


class AdapterConfig(BaseModel):
    """One MCP adapter the tenant exposes: transport, URL (from env) and whether it is required."""

    model_config = ConfigDict(frozen=True)

    transport: str
    url: str
    required: bool = True


class DepartmentConfig(BaseModel):
    """A human department: display name and the channel tickets are announced on."""

    model_config = ConfigDict(frozen=True)

    display_name_en: str
    channel: str


class CustomerIdentifierConfig(BaseModel):
    """How this tenant names customers (label, input pattern, example); drives the login form."""

    model_config = ConfigDict(frozen=True)

    label_en: str
    pattern: str
    example: str


class PersonaConfig(BaseModel):
    """The assistant's voice for this tenant: name, tone, language and standing disclaimers."""

    model_config = ConfigDict(frozen=True)

    name_en: str
    tone_en: str
    language: str
    disclaimers_en: list[str] = Field(default_factory=list)


class TenantFile(BaseModel):
    """`tenant.yaml`: identity, locale, adapters, departments and persona."""

    model_config = ConfigDict(frozen=True)

    version: int
    tenant: str
    display_name: str
    locale: str
    timezone: str
    currency: str
    business_domain: str
    customer_identifier: CustomerIdentifierConfig
    adapters: dict[str, AdapterConfig]
    departments: dict[str, DepartmentConfig]
    persona: PersonaConfig


# --- policy.yaml -------------------------------------------------------------


class Condition(BaseModel):
    """One `conditions[]` entry, evaluated against a dotted path in the action context."""

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    field: str
    eq: Any = None
    in_: list[Any] | None = Field(default=None, alias="in")
    lt: float | None = None
    lte: float | None = None
    gt: float | None = None
    gte: float | None = None
    ne: Any = None
    exists: bool | None = None


class OnConditionFail(BaseModel):
    """What happens when an action's conditions fail: which department takes over and why."""

    model_config = ConfigDict(frozen=True)

    escalate_to: str | None = None
    reason_code: str | None = None


class RateLimit(BaseModel):
    """Caps on how often an action may run (per conversation and per customer per day)."""

    model_config = ConfigDict(frozen=True)

    per_conversation: int | None = None
    per_customer_per_day: int | None = None


class ActionPolicy(BaseModel):
    """Policy for one action: allowed or not, confirmation, conditions, limits and amount cap."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    requires_confirmation: bool = False
    description_en: str | None = None
    conditions: list[Condition] = Field(default_factory=list)
    on_condition_fail: OnConditionFail | None = None
    rate_limit: RateLimit | None = None
    max_amount_gbp: float | None = None
    escalate_to: str | None = None
    reason_code: str | None = None
    reason_en: str | None = None


class DecisionPolicyConfig(BaseModel):
    """Confidence floor for model decisions (see `decision.fallback`)."""

    model_config = ConfigDict(frozen=True)

    min_confidence: float = 0.6
    escalate_on_low_confidence: bool = True


class LimitsConfig(BaseModel):
    """Hard limits per turn and per conversation (tool calls, questions, actions, investigation steps)."""

    model_config = ConfigDict(frozen=True)

    max_tool_calls_per_turn: int = 8
    max_questions_advisory: int = 5
    max_actions_per_conversation: int = 3
    # Agentic investigation (contracts §4.3): model steps per turn, and how many times the
    # assistant may ask the customer a clarifying question before handing over to a human.
    max_investigation_steps: int = Field(default=4, ge=1, le=10)
    max_clarification_rounds: int = Field(default=2, ge=0, le=5)


class PiiConfig(BaseModel):
    """Which fields are masked before the model, and which identifiers must stay readable."""

    model_config = ConfigDict(frozen=True)

    mask_before_model: list[str] = Field(default_factory=list)
    allow_unmasked: list[str] = Field(default_factory=list)
    mask_in_traces: bool = True
    mask_in_tickets: bool = True


class IncidentHandlingConfig(BaseModel):
    """How known incidents are handled (attach to the existing ticket, do not open new ones)."""

    model_config = ConfigDict(frozen=True)

    attach_to_existing_incident: bool = True
    create_ticket_for_known_incident: bool = False
    max_tickets_per_incident: int = 1


class PolicyFile(BaseModel):
    """`policy.yaml`: the authority rules `policy.engine.PolicyEngine` interprets."""

    model_config = ConfigDict(frozen=True)

    version: int
    tenant: str
    decision: DecisionPolicyConfig
    limits: LimitsConfig
    actions: dict[str, ActionPolicy]
    pii: PiiConfig
    incident_handling: IncidentHandlingConfig


# --- routing.yaml ------------------------------------------------------------


class RecommendationWeights(BaseModel):
    """Relative weight of each scoring component in `recommendation.engine`."""

    model_config = ConfigDict(frozen=True)

    speed_adequacy: float
    budget_fit: float
    profile_match: float
    commitment_match: float
    extras_match: float


class RecommendationConfig(BaseModel):
    """Scoring parameters and reason templates for package recommendation."""

    model_config = ConfigDict(frozen=True)

    weights: RecommendationWeights
    mbps_per_device: float
    budget_hard_filter_tolerance: float
    min_mbps_floor: float
    # Devices assumed per person when only `household_size` is known (deterministic estimate).
    devices_per_person: float = 3.0
    # A package offering less than this fraction of the household's required speed is not
    # offered at all — unless nothing else survives the filters, in which case the customer
    # still sees the closest option and the reply says it is tight.
    speed_hard_filter_ratio: float = 0.5
    usage_profile_map: dict[str, list[str]]
    reason_codes_en: dict[str, str]


class RoutingFile(BaseModel):
    """`routing.yaml`: recommendation scoring, issue -> department routing and urgency floors."""

    model_config = ConfigDict(frozen=True)

    version: int
    tenant: str
    recommendation: RecommendationConfig
    issue_routing: dict[str, str]
    urgency_floor: dict[str, str]
    diagnostic_checklist: list[str]
    # READ-ONLY tool names the investigation model may call (contracts §4.3). Never write
    # tools: validated against `policy.yaml: actions` in `TenantConfig`.
    investigation_tools: list[str] = Field(default_factory=list)


# --- Combined config ---------------------------------------------------------


# A tool whose name starts like this mutates something, so it must never be offered to the
# model as an investigation tool (the investigation may only read).
_WRITE_TOOL_PREFIXES = (
    "create_", "post_", "add_", "retry_", "enqueue_", "resend_", "apply_", "request_",
    "reschedule_", "update_", "delete_", "send_", "issue_", "set_", "cancel_", "repair_",
)


class TenantConfig(BaseModel):
    """Everything loaded for one tenant: `tenant.yaml`, `policy.yaml` and `routing.yaml`.

    The validator enforces a safety rule at load time: the investigation tools the model may
    call are read-only (no duplicates, none named in `policy.actions`, none with a write verb).
    """

    model_config = ConfigDict(frozen=True)

    tenant_name: str
    tenant: TenantFile
    policy: PolicyFile
    routing: RoutingFile

    @model_validator(mode="after")
    def _investigation_tools_are_read_only(self) -> "TenantConfig":
        tools = self.routing.investigation_tools
        if len(set(tools)) != len(tools):
            raise ValueError("routing.yaml investigation_tools contains duplicates")
        bad = sorted(
            t for t in tools if t in self.policy.actions or t.startswith(_WRITE_TOOL_PREFIXES)
        )
        if bad:
            raise ValueError(
                f"routing.yaml investigation_tools must be read-only, but lists write tools: {bad}"
            )
        return self

    # Shortcuts so callers write `config.adapters` instead of `config.tenant.adapters`.
    @property
    def adapters(self) -> dict[str, AdapterConfig]:
        return self.tenant.adapters

    @property
    def departments(self) -> dict[str, DepartmentConfig]:
        return self.tenant.departments

    @property
    def persona(self) -> PersonaConfig:
        return self.tenant.persona
