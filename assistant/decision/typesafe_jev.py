"""Documented stub for an external "TypeSafe JEV" decision model (contracts §4.5:
`DECISION_SERVICE=typesafe_jev`). Not a real implementation — selectable today so the
interface boundary and config switch exist before a real adapter is built; every method
raises `NotImplementedError` describing exactly what a real adapter must accept and
return to be plugged in.

Import as: ``from decision.typesafe_jev import TypeSafeJevDecisionService``.
"""
from __future__ import annotations

from typing import Any

from core_common.types import Decision, Department, Intent, IssueType, Priority
from decision.base import DecisionContext

ADAPTER_CONTRACT = """\
A real `typesafe_jev` adapter must implement the same four methods as
`decision.base.DecisionService`, with this exact surface:

INPUTS (every method):
    ctx: decision.base.DecisionContext
        - ctx.history: list[{"role": "user"|"assistant", "content": str}] — already
          PII-masked conversation turns, oldest first. The adapter must never attempt to
          reverse this masking.
        - ctx.masked_customer_facts: dict[str, Any] — masked facts already known about
          the customer/subscription (e.g. subscription status, region) gathered during
          this conversation.
        - ctx.diagnosis: core_common.types.Diagnosis | None — the diagnostic mode's
          finding so far, if any (root_cause, scope, confidence, evidence).
        - ctx.advisory_profile: core_common.types.AdvisoryProfile | None — the advisory
          profile collected so far, if in ADVISORY mode.
        - ctx.tenant_config_slice: dict[str, Any] — tenant-specific hints (e.g. known
          issue-type/department vocabularies); never adapter URLs or secrets.

OUTPUTS (every method): core_common.types.Decision[T] where T is:
        classify_intent       -> core_common.types.Intent
        choose_department     -> core_common.types.Department
        assess_urgency        -> core_common.types.Priority
        classify_issue_type   -> core_common.types.IssueType

    Decision.value:      MUST be a member of the corresponding enum — never a free string.
    Decision.confidence: float in [0, 1], CALIBRATED — 0.6 should mean "right about 60%
        of the time on held-out data", not a fixed/placeholder constant. When the adapter
        cannot support a claim with real confidence, it must return a low (ideally 0.0)
        confidence rather than a guessed high one. The caller applies a confidence floor
        (decision.fallback.apply_confidence_floor) and treats low confidence as "escalate
        to a human", never as "retry with a guess".
    Decision.rationale:  short, English, human-readable justification — this goes into the
        audit log (contracts §4.8) verbatim, so it must never contain customer-facing
        text or raw PII.
    Decision.model:      identifies the concrete backing model/version string.
    Decision.raw:        the adapter's raw structured response, for debugging/audit,
        already free of PII.

FAILURE SEMANTICS: this adapter must never raise for an ordinary "I don't know" — return a
Decision with low/zero confidence instead, so a turn never crashes on an uncertain answer.
Raising is reserved for adapter-infrastructure failures the caller cannot otherwise handle
(e.g. construction with a missing endpoint/key, as `llm.openai_agents.OpenAIAgentsProvider`
does for a missing API key).
"""


class TypeSafeJevDecisionService:
    """Stub for `DECISION_SERVICE=typesafe_jev`. See `ADAPTER_CONTRACT` above for exactly
    what a real integration must implement; every method here raises `NotImplementedError`."""

    def __init__(self, **kwargs: Any) -> None:
        # Accepted so `decision.factory.get_decision_service` can construct this the same
        # way as the real implementation, without special-casing the stub.
        self._kwargs = kwargs

    def classify_intent(self, ctx: DecisionContext) -> Decision[Intent]:
        raise NotImplementedError(ADAPTER_CONTRACT)

    def choose_department(self, ctx: DecisionContext) -> Decision[Department]:
        raise NotImplementedError(ADAPTER_CONTRACT)

    def assess_urgency(self, ctx: DecisionContext) -> Decision[Priority]:
        raise NotImplementedError(ADAPTER_CONTRACT)

    def classify_issue_type(self, ctx: DecisionContext) -> Decision[IssueType]:
        raise NotImplementedError(ADAPTER_CONTRACT)
