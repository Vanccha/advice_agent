"""The ``DecisionService`` Protocol and the context every decision call receives.

Where this sits: ``decision`` package, bottom layer. ``modes`` call a ``DecisionService``;
``llm_structured`` and ``typesafe_jev`` implement it; ``factory`` picks one.

Key ideas:
- Replaceable judgement: the model only classifies. Authority (what may happen) stays in
  ``policy``, so swapping the decision model cannot widen what the assistant is allowed to do.
- Every answer is a ``Decision[T]`` (value, confidence, rationale, model); callers apply a
  confidence floor instead of trusting the value blindly.
- Context is already masked: this layer sits downstream of the PII boundary.

Read next: ``decision/llm_structured.py``, ``decision/fallback.py``.
See: docs/contracts.md §4.5.
"""
from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from core_common.types import AdvisoryProfile, Decision, Department, Diagnosis, Intent, IssueType, Priority, Triage


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

    def triage(self, ctx: DecisionContext) -> Triage:
        """Issue type + priority + department in ONE round-trip (same context for all
        three). Services that only implement the three methods above can delegate to
        `triage_via_individual_calls`."""
        ...


def triage_via_individual_calls(service: Any, ctx: DecisionContext) -> Triage:
    """Default `triage` for any service exposing just `classify_issue_type`,
    `assess_urgency` and `choose_department` (three calls, as before the combined call)."""
    return Triage(
        issue_type=service.classify_issue_type(ctx),
        priority=service.assess_urgency(ctx),
        department=service.choose_department(ctx),
    )
