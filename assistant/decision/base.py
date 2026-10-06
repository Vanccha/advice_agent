"""`DecisionService` Protocol (contracts §4.5) — replaceable judgement.

Import as: ``from decision.base import DecisionService, DecisionContext``.
`Decision[T]` itself is reused unchanged from `core_common.types` (never redefined here).
"""
from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from core_common.types import AdvisoryProfile, Decision, Department, Diagnosis, Intent, IssueType, Priority


class DecisionContext(BaseModel):
    """Everything a `DecisionService` call may look at. Every string field here is
    expected to already be masked (contracts §4.7) — the decision layer is downstream of
    the PII boundary, not responsible for enforcing it itself (that is `llm.guard`, which
    every provider call still passes through as a second, independent check).
    """

    model_config = ConfigDict(frozen=True)

    conversation_id: str
    tenant: str
    # Masked conversation turns, oldest first: [{"role": "user"|"assistant", "content": "..."}].
    history: list[dict[str, str]] = Field(default_factory=list)
    # e.g. {"customer_no": "NS-100042", "subscription_status": "active", ...} — masked.
    masked_customer_facts: dict[str, Any] = Field(default_factory=dict)
    diagnosis: Diagnosis | None = None
    advisory_profile: AdvisoryProfile | None = None
    # Slices of the tenant's policy/routing config the decision may use as hints (e.g.
    # the list of known issue types, department display names) — never adapter URLs/secrets.
    tenant_config_slice: dict[str, Any] = Field(default_factory=dict)


class DecisionService(Protocol):
    """Contracts §4.5. Every method returns a `Decision[T]`; implementations must never
    raise for an ordinary low-confidence or out-of-enum answer — see
    `decision.llm_structured` and `decision.fallback.apply_confidence_floor`."""

    def classify_intent(self, ctx: DecisionContext) -> Decision[Intent]: ...

    def choose_department(self, ctx: DecisionContext) -> Decision[Department]: ...

    def assess_urgency(self, ctx: DecisionContext) -> Decision[Priority]: ...

    def classify_issue_type(self, ctx: DecisionContext) -> Decision[IssueType]: ...
