"""The advisory question flow: parse an answer, ask the next question or recommend.

Where this sits: `modes.dispatch` calls `handle_turn` once per customer message while the
conversation is in ADVISORY mode.

Key ideas:
- Bounded: at most ``policy.yaml: limits.max_questions_advisory`` questions, then it
  recommends with what it has.
- Recommendation is deterministic (`recommendation.engine`); the model only narrates, and
  `narration.py` falls back to a fixed template on any doubt.
- The opening message is never parsed as an answer: it is the request that got us here.

Read next: modes/advisory/narration.py, recommendation/questions.py.
See: docs/contracts.md §4.3, §4.4.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core_common.types import AdvisoryProfile, StepType
from modes import replies
from modes.advisory.extraction import apply_answer, apply_opening_message
from modes.advisory.narration import verbalize_offers, verbalize_with_model
from modes.context import TurnContext
from modes.tool_data import as_list
from recommendation.engine import recommend_packages
from recommendation.questions import next_question, profile_is_complete

@dataclass
class AdvisoryStepResult:
    reply_en: str
    done: bool
    profile: AdvisoryProfile
    awaiting_field: str | None
    questions_asked: int
    offers: list[dict[str, Any]] = field(default_factory=list)
    # True when the message was not an answer to the question we asked, and the customer
    # appears to be raising something else — the orchestrator re-routes instead of pushing
    # them through the remaining questions.
    reroute: bool = False
    nudges: int = 0



def handle_turn(
    ctx: TurnContext,
    *,
    profile: AdvisoryProfile,
    awaiting_field: str | None,
    questions_asked: int,
    masked_message: str | None,
    is_first_turn: bool,
) -> AdvisoryStepResult:
    """Advance ADVISORY mode by exactly one step.

    ``is_first_turn``: True the very turn the router just handed control to ADVISORY —
    the incoming message was the complaint/request that got us here, not an answer to a
    question we haven't asked yet, so it is never parsed as an answer.
    """
    max_questions = ctx.tenant_config.policy.limits.max_questions_advisory

    if is_first_turn and masked_message:
        profile, filled = apply_opening_message(profile, masked_message, ctx)
        if filled:
            ctx.audit_log.append(
                ctx.conversation_id,
                StepType.MODE_DECISION,
                f"advisory: opening message already stated {', '.join(filled)}",
                "extracted from the first message (model extraction + labelled patterns); "
                "only the missing questions are asked",
                {"fields": filled},
                tenant=ctx.tenant,
            )

    if not is_first_turn and awaiting_field and masked_message is not None:
        profile, _understood = apply_answer(profile, awaiting_field, masked_message, ctx)
        questions_asked += 1
        ctx.audit_log.append(
            ctx.conversation_id,
            StepType.MODE_DECISION,
            f"advisory: recorded answer for '{awaiting_field}'",
            "parsed via deterministic field parser (no LLM)",
            {"field": awaiting_field},
            tenant=ctx.tenant,
        )

    complete = profile_is_complete(profile)
    question = None if (complete or questions_asked >= max_questions) else next_question(profile)

    if question is not None:
        return AdvisoryStepResult(
            reply_en=question.question_en,
            done=False,
            profile=profile,
            awaiting_field=question.field,
            questions_asked=questions_asked,
        )

    # All done (or question budget spent): fetch the live catalogue and recommend.
    catalog_outcome = ctx.call_tool("list_packages", {})
    packages = as_list(catalog_outcome)

    offers = recommend_packages(profile, packages, ctx.tenant_config.routing)
    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.ACTION,
        "recommend_packages produced package offers",
        "deterministic scoring, no LLM package choice",
        {"offer_codes": [o.package_code for o in offers], "scores": [o.score for o in offers]},
        tenant=ctx.tenant,
    )

    model_reply = verbalize_with_model(ctx, offers, packages) if offers else None
    if model_reply is not None:
        reply_en = model_reply
        ctx.audit_log.append(
            ctx.conversation_id,
            StepType.MODE_DECISION,
            "advisory: model verbalized the engine's offers",
            "reply validated against PackageOffer names/order/price/speed before use",
            {"offer_codes": [o.package_code for o in offers]},
            tenant=ctx.tenant,
        )
    else:
        reply_en = verbalize_offers(offers)
        ctx.audit_log.append(
            ctx.conversation_id,
            StepType.MODE_DECISION,
            "advisory: used deterministic verbalization template",
            (
                "no packages to recommend"
                if not offers
                else "model reply missing/invalid or provider error; fixed template used instead"
            ),
            {"offer_codes": [o.package_code for o in offers]},
            tenant=ctx.tenant,
        )

    if offers and not complete and questions_asked >= max_questions:
        reply_en += replies.ADVISORY_QUESTION_LIMIT_NOTE

    return AdvisoryStepResult(
        reply_en=reply_en,
        done=True,
        profile=profile,
        awaiting_field=None,
        questions_asked=questions_asked,
        offers=[o.model_dump(mode="json") for o in offers],
    )
