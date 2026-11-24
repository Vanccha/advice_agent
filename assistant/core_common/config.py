"""Tenant configuration loader.

Reads ``<TENANT_CONFIG_DIR>/<tenant>/{tenant,policy,routing}.yaml``, expands ``${ENV_VAR}``
placeholders from ``os.environ``, validates every file against a Pydantic model, and caches
the result per tenant. Works unchanged for ``netswift`` and ``_example``.

Import as: ``from core_common.config import load_tenant_config, TenantConfig, ConfigError``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from core_common.settings import get_settings

# --------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------


class ConfigError(RuntimeError):
    """Raised for any missing/invalid tenant configuration (including unset env vars)."""


# --------------------------------------------------------------------------------------
# tenant.yaml
# --------------------------------------------------------------------------------------


class AdapterConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    transport: str
    url: str
    required: bool = True


class DepartmentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    display_name_en: str
    channel: str


class CustomerIdentifierConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    label_en: str
    pattern: str
    example: str


class PersonaConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    name_en: str
    tone_en: str
    language: str
    disclaimers_en: list[str] = Field(default_factory=list)


class TenantFile(BaseModel):
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


# --------------------------------------------------------------------------------------
# policy.yaml
# --------------------------------------------------------------------------------------


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
    model_config = ConfigDict(frozen=True)

    escalate_to: str | None = None
    reason_code: str | None = None


class RateLimit(BaseModel):
    model_config = ConfigDict(frozen=True)

    per_conversation: int | None = None
    per_customer_per_day: int | None = None


class ActionPolicy(BaseModel):
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
    model_config = ConfigDict(frozen=True)

    min_confidence: float = 0.6
    escalate_on_low_confidence: bool = True


class LimitsConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_tool_calls_per_turn: int = 8
    max_questions_advisory: int = 5
    max_actions_per_conversation: int = 3
    # Agentic investigation (contracts §4.3): model steps per turn, and how many times the
    # assistant may ask the customer a clarifying question before handing over to a human.
    max_investigation_steps: int = Field(default=4, ge=1, le=10)
    max_clarification_rounds: int = Field(default=2, ge=0, le=5)


class PiiConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    mask_before_model: list[str] = Field(default_factory=list)
    allow_unmasked: list[str] = Field(default_factory=list)
    mask_in_traces: bool = True
    mask_in_tickets: bool = True


class IncidentHandlingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    attach_to_existing_incident: bool = True
    create_ticket_for_known_incident: bool = False
    max_tickets_per_incident: int = 1


class PolicyFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int
    tenant: str
    decision: DecisionPolicyConfig
    limits: LimitsConfig
    actions: dict[str, ActionPolicy]
    pii: PiiConfig
    incident_handling: IncidentHandlingConfig


# --------------------------------------------------------------------------------------
# routing.yaml
# --------------------------------------------------------------------------------------


class RecommendationWeights(BaseModel):
    model_config = ConfigDict(frozen=True)

    speed_adequacy: float
    budget_fit: float
    profile_match: float
    commitment_match: float
    extras_match: float


class RecommendationConfig(BaseModel):
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


# --------------------------------------------------------------------------------------
# Combined config
# --------------------------------------------------------------------------------------


# A tool whose name starts like this mutates something — never exposable to the model.
_WRITE_TOOL_PREFIXES = (
    "create_", "post_", "add_", "retry_", "enqueue_", "resend_", "apply_", "request_",
    "reschedule_", "update_", "delete_", "send_", "issue_", "set_", "cancel_", "repair_",
)


class TenantConfig(BaseModel):
    """Everything loaded for one tenant. Cached per (tenant, config_dir)."""

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

    @property
    def adapters(self) -> dict[str, AdapterConfig]:
        return self.tenant.adapters

    @property
    def departments(self) -> dict[str, DepartmentConfig]:
        return self.tenant.departments

    @property
    def persona(self) -> PersonaConfig:
        return self.tenant.persona


# --------------------------------------------------------------------------------------
# Env var expansion
# --------------------------------------------------------------------------------------

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(node: Any) -> Any:
    if isinstance(node, str):

        def _sub(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in os.environ:
                raise ConfigError(
                    f"Environment variable '{name}' referenced in tenant config is not set"
                )
            return os.environ[name]

        return _ENV_PATTERN.sub(_sub, node)
    if isinstance(node, dict):
        return {key: _expand_env(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_expand_env(value) for value in node]
    return node


# --------------------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------------------

_CACHE: dict[tuple[str, str], TenantConfig] = {}


def _load_yaml_model(path: Path, model: type[BaseModel], tenant_name: str) -> Any:
    if not path.is_file():
        raise ConfigError(f"Missing tenant config file: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse {path}: {exc}") from exc
    expanded = _expand_env(raw)
    try:
        return model.model_validate(expanded)
    except ValidationError as exc:
        raise ConfigError(f"Invalid {path.name} for tenant '{tenant_name}': {exc}") from exc


def load_tenant_config(tenant: str | None = None, config_dir: str | os.PathLike[str] | None = None) -> TenantConfig:
    """Load (and cache) the full configuration for one tenant.

    Args:
        tenant: tenant directory name (e.g. ``"netswift"``, ``"_example"``). Defaults to
            ``get_settings().TENANT``.
        config_dir: overrides ``get_settings().TENANT_CONFIG_DIR`` — mainly for tests.
    """
    settings = get_settings()
    tenant_name = tenant or settings.TENANT
    base_dir = Path(config_dir or settings.TENANT_CONFIG_DIR)

    cache_key = (tenant_name, str(base_dir))
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    tenant_dir = base_dir / tenant_name
    if not tenant_dir.is_dir():
        raise ConfigError(f"Tenant config directory not found: {tenant_dir}")

    tenant_file = _load_yaml_model(tenant_dir / "tenant.yaml", TenantFile, tenant_name)
    policy_file = _load_yaml_model(tenant_dir / "policy.yaml", PolicyFile, tenant_name)
    routing_file = _load_yaml_model(tenant_dir / "routing.yaml", RoutingFile, tenant_name)

    try:
        config = TenantConfig(
            tenant_name=tenant_name,
            tenant=tenant_file,
            policy=policy_file,
            routing=routing_file,
        )
    except ValidationError as exc:
        raise ConfigError(f"Invalid tenant configuration for '{tenant_name}': {exc}") from exc
    _CACHE[cache_key] = config
    return config


def clear_tenant_config_cache() -> None:
    """Test helper: forget every cached tenant config."""
    _CACHE.clear()
