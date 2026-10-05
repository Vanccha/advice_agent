"""The confidence floor (contracts §4.5 / §4.6): a `DecisionService` call below
`policy.decision.min_confidence` must never silently drive routing or urgency. Replace it
with the deterministic table in `routing.yaml` and flag it so the caller hands over to a
human instead of guessing.

Import as: ``from decision.fallback import apply_confidence_floor, FallbackResult``.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from core_common.config import RoutingFile
from core_common.types import Decision, Department, Priority

#: Public so callers that need to enforce `routing.yaml: urgency_floor` themselves (e.g.
#: `modes.action`, which must never let urgency drop below the floor even for a *confident*
#: decision — this function only raises-to-floor in the low-confidence branch below) can
#: reuse the same ranking instead of redefining it.
PRIORITY_ORDER = {"LOW": 0, "NORMAL": 1, "HIGH": 2, "URGENT": 3}
_PRIORITY_ORDER = PRIORITY_ORDER


class FallbackResult(BaseModel):
    """What `apply_confidence_floor` hands back: the `Decision` to actually use, and
    whether it was replaced/flagged. `escalated=True` means: hand this conversation to a
    human (ticket/approval) instead of trusting the model's own guess."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    decision: Any  # Decision[Department] | Decision[Priority] | Decision[Intent] | Decision[IssueType]
    escalated: bool


def apply_confidence_floor(
    decision: Decision,
    min_confidence: float,
    routing_config: RoutingFile,
    *,
    issue_type: str | None = None,
) -> FallbackResult:
    """Apply the confidence floor to a single `Decision`.

    - `decision.confidence >= min_confidence`: returned unchanged, `escalated=False`.
    - otherwise, below the floor:
        - a `Department`-valued decision, with a known `issue_type`, is replaced by
          `routing_config.issue_routing[issue_type]` (confidence pinned to 0.0),
          `escalated=True` — this is the `issue_routing` fallback table from contracts §4.5.
        - a `Priority`-valued decision is never lowered: if `issue_type` is known and
          `routing_config.urgency_floor[issue_type]` outranks the model's own value, the
          floor value is used instead; otherwise the model's value is kept. Either way
          `escalated=True`, since a low-confidence urgency call should still hand over.
        - any other case (no applicable table, e.g. `Intent`/`IssueType` decisions, or no
          `issue_type` given) returns the original decision unchanged, but `escalated=True`
          so the caller still knows not to trust it on its own.

    `issue_type` is keyword-only and optional: the department/urgency fallback tables are
    keyed by issue type (contracts §4.5 `routing.yaml: issue_routing`/`urgency_floor`), so
    callers that already know the issue type for this turn should pass it; callers that
    don't (e.g. the very call classifying intent, before an issue type exists) simply get
    the "escalate, no table substitution" branch.
    """
    if decision.confidence >= min_confidence:
        return FallbackResult(decision=decision, escalated=False)

    if isinstance(decision.value, Department) and issue_type and issue_type in routing_config.issue_routing:
        fallback_department = Department(routing_config.issue_routing[issue_type])
        replaced = Decision(
            value=fallback_department,
            confidence=0.0,
            rationale=(
                f"confidence {decision.confidence:.2f} below floor {min_confidence:.2f}; "
                f"routed via routing.yaml issue_routing[{issue_type!r}]"
            ),
            model=decision.model,
            raw={"original": decision.model_dump(mode="json")},
        )
        return FallbackResult(decision=replaced, escalated=True)

    if isinstance(decision.value, Priority):
        if issue_type and issue_type in routing_config.urgency_floor:
            floor_priority = Priority(routing_config.urgency_floor[issue_type])
            if _PRIORITY_ORDER[floor_priority.value] > _PRIORITY_ORDER[decision.value.value]:
                replaced = Decision(
                    value=floor_priority,
                    confidence=0.0,
                    rationale=(
                        f"confidence {decision.confidence:.2f} below floor {min_confidence:.2f}; "
                        f"raised to routing.yaml urgency_floor[{issue_type!r}]={floor_priority.value}"
                    ),
                    model=decision.model,
                    raw={"original": decision.model_dump(mode="json")},
                )
                return FallbackResult(decision=replaced, escalated=True)
        return FallbackResult(decision=decision, escalated=True)

    return FallbackResult(decision=decision, escalated=True)
