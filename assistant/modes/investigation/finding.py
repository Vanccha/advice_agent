"""The investigation's structured conclusion: its type, policy checks and customer wording.

Where this sits: `modes.investigation`. The model ends by calling the local `submit_finding`
tool; `loop.py` validates the arguments into an `InvestigationFinding` and passes it through
`finalize_finding`. `modes.action` later turns the finding into a reply or a ticket.

Key ideas:
- A finding never carries authority: it only shapes a customer reply or a ticket. No write
  action is reachable from it.
- `finalize_finding` is where policy overrides the model: low confidence, a missing
  question or exhausted clarification rounds all become a human handover, never a guess.
- Every conclusion is audited with both the model's outcome and the one actually used.

Read next: modes/investigation/loop.py, modes/action/investigation_outcome.py.
"""
from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core_common.types import Department, StepType
from llm.base import ToolDefinition
from modes import replies
from modes.context import TurnContext
from modes.investigation.tools import audit
from observability.tracing import record_decision
from privacy.masking import mask_text

# The name of the one local tool that ends an investigation.
SUBMIT_FINDING = "submit_finding"


# --- Finding type -------------------------------------------------------------------
class FindingOutcome(StrEnum):
    NEEDS_MORE_INFO = "needs_more_info"
    ESCALATE = "escalate"
    RESOLVED_INFO = "resolved_info"


class InvestigationFinding(BaseModel):
    """The model's structured conclusion (validated; never trusted for authority)."""

    model_config = ConfigDict(frozen=True)

    outcome: FindingOutcome = Field(
        description=(
            "needs_more_info: ask the customer one clarifying question. escalate: a human "
            "team must look at it. resolved_info: the records show nothing wrong and the "
            "customer can simply be informed."
        )
    )
    summary_en: str = Field(
        min_length=1,
        description="Neutral summary for the department ticket. No personal data.",
    )
    customer_reply_en: str = Field(
        min_length=1,
        description=(
            "Context for the customer in plain British English: what you found. Never put a "
            "question here (questions go ONLY in clarifying_question_en) and do not mention tickets."
        ),
    )
    clarifying_question_en: str | None = Field(
        default=None,
        description="Exactly one NEW question, required when outcome is needs_more_info; never repeats an earlier one.",
    )
    suggested_department: str | None = Field(
        default=None,
        description="One of TECHNICAL_INFRA, BILLING, SUBSCRIPTION_OPS, FIELD_INSTALL.",
    )
    confidence: float = Field(ge=0.0, le=1.0, description="0..1 confidence in this conclusion.")
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="Identifiers and statuses you relied on (ids, status words). No names or contact details.",
    )


def submit_tool() -> ToolDefinition:
    """The local tool whose JSON schema is the `InvestigationFinding` model itself."""
    return ToolDefinition(
        name=SUBMIT_FINDING,
        description="Submit your final finding. Call this exactly once, when you are done.",
        input_schema=InvestigationFinding.model_json_schema(),
    )


# --- Parsing and policy -------------------------------------------------------------
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_text_finding(text: str | None) -> InvestigationFinding | None:
    if not text:
        return None
    candidate = _FENCE.sub("", text.strip())
    try:
        return InvestigationFinding.model_validate_json(candidate)
    except ValidationError:
        return None


def resolve_department(ctx: TurnContext, suggested: str | None) -> tuple[Department, bool]:
    """The finding's department when it is a real one; otherwise the tenant's own
    ``routing.yaml: issue_routing[other]`` table. Returns (department, used_fallback)."""
    if suggested:
        try:
            return Department(suggested.strip().upper()), False
        except ValueError:
            pass
    fallback = ctx.tenant_config.routing.issue_routing.get("other")
    try:
        return Department(fallback), True
    except ValueError:
        return Department.SUBSCRIPTION_OPS, True


def _handover(
    finding: InvestigationFinding, *, summary_prefix: str, reply_en: str
) -> InvestigationFinding:
    return finding.model_copy(
        update={
            "outcome": FindingOutcome.ESCALATE,
            "summary_en": f"{summary_prefix} {finding.summary_en}".strip(),
            "customer_reply_en": reply_en,
            "clarifying_question_en": None,
        }
    )


def finalize_finding(
    ctx: TurnContext, finding: InvestigationFinding, *, allow_clarification: bool
) -> InvestigationFinding:
    """Apply policy to the model's conclusion: low confidence and exhausted clarification
    rounds both end in a human, never a guess. Audited either way."""
    min_confidence = ctx.tenant_config.policy.decision.min_confidence
    final = finding
    note = ""
    if finding.outcome is FindingOutcome.NEEDS_MORE_INFO:
        if not finding.clarifying_question_en or not finding.clarifying_question_en.strip():
            final = _handover(
                finding,
                summary_prefix="Investigation could not form a usable question.",
                reply_en=replies.INVESTIGATION_NO_QUESTION,
            )
            note = " (no clarifying question supplied; handed over)"
        elif not allow_clarification:
            final = _handover(
                finding,
                summary_prefix="The customer has already been asked for more detail and the problem is still unclear.",
                reply_en=replies.INVESTIGATION_ROUNDS_EXHAUSTED,
            )
            note = " (clarification rounds exhausted; handed over)"
    elif (
        finding.outcome is FindingOutcome.RESOLVED_INFO
        and finding.confidence < min_confidence
        and ctx.tenant_config.policy.decision.escalate_on_low_confidence
    ):
        final = _handover(
            finding,
            summary_prefix=f"Low-confidence investigation ({finding.confidence:.2f}); not told to the customer as fact.",
            reply_en=replies.INVESTIGATION_LOW_CONFIDENCE,
        )
        note = " (low confidence; handed over)"
    elif finding.outcome is FindingOutcome.ESCALATE and finding.confidence < min_confidence:
        note = " (low confidence; escalation kept)"

    department, used_fallback = resolve_department(ctx, final.suggested_department)
    if final.outcome is FindingOutcome.ESCALATE:
        final = final.model_copy(update={"suggested_department": department.value})
        if used_fallback:
            note += " (department not recognised; routed via issue_routing)"

    audit(
        ctx,
        f"investigation finding: {final.outcome.value}{note}",
        final.summary_en,
        {
            "model_outcome": finding.outcome.value,
            "used_outcome": final.outcome.value,
            "confidence": finding.confidence,
            "department": department.value if final.outcome is FindingOutcome.ESCALATE else None,
            "evidence_keys": sorted(str(k) for k in finding.evidence),
        },
        StepType.DECISION_SERVICE,
    )
    record_decision(
        "investigation_finding",
        value=final.outcome.value,
        confidence=finding.confidence,
        rationale=mask_text(final.summary_en).masked,
        model=ctx.provider.model,
    )
    return final



# --- Customer wording ---------------------------------------------------------------
def compose_question_reply(context_en: str, question: str) -> str:
    """Context first, then exactly one question. Questions the model left inside the context
    text are dropped so the customer is never asked twice."""
    sentences = re.split(r"(?<=[.!?])\s+", context_en.strip())
    context = " ".join(x for x in sentences if x and not x.endswith("?")).strip()
    if not question:
        return context or context_en.strip()
    return f"{context} {question}".strip()
