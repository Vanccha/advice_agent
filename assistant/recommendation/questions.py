"""Fixed question bank for ADVISORY mode: what to ask, in what order, and when to stop.

Where this sits: ``recommendation`` package. ``modes.advisory`` calls ``next_question`` and
``profile_is_complete`` after each customer answer; ``AdvisoryProfile`` is in ``core_common.types``.

Key ideas:
- Fixed questions, not model-invented ones: the dialogue is predictable and testable, and the
  model only extracts answers into the profile.
- Order is the data: ``QUESTIONS`` order is the order the customer is asked in.
- Early exit: the profile counts as complete once usage, size and budget are known.

Read next: ``recommendation/engine.py``.
See: docs/contracts.md §4.3.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from core_common.types import AdvisoryProfile


class Question(BaseModel):
    """One question the assistant can ask, tied to the `AdvisoryProfile` field it fills."""

    model_config = ConfigDict(frozen=True)

    field: str
    question_en: str
    expected_type: str
    options_en: list[str] | None = None


# Order matters: this is also the order the assistant asks them in.
QUESTIONS: list[Question] = [
    Question(
        field="usage",
        question_en=(
            "What do you mainly use the internet for? You can pick more than one."
        ),
        expected_type="multi_choice",
        options_en=["Studying", "Family", "Home office", "Gaming", "Streaming", "Everyday use"],
    ),
    Question(
        field="household_size",
        question_en="How many people live in your home?",
        expected_type="int",
    ),
    Question(
        field="device_count",
        question_en="Roughly how many devices connect to the internet (phones, computers, TVs, etc.)?",
        expected_type="int",
    ),
    Question(
        field="budget_gbp",
        question_en=(
            "Roughly what monthly budget do you have in mind (£)? You can skip this if "
            "you would rather not say."
        ),
        expected_type="float",
    ),
    Question(
        field="commitment_preference",
        question_en="Do you have a contract preference?",
        expected_type="enum",
        options_en=["No contract", "12 months", "24 months", "No preference"],
    ),
]


def _is_missing(profile: AdvisoryProfile, field: str) -> bool:
    """Unanswered means empty list for `usage`, `None` for every other field."""
    value = getattr(profile, field)
    if field == "usage":
        return not value
    return value is None


def next_question(profile: AdvisoryProfile) -> Question | None:
    """The next unanswered question, in fixed order. ``None`` once all 5 are answered."""
    for question in QUESTIONS:
        if _is_missing(profile, question.field):
            return question
    return None


def profile_is_complete(profile: AdvisoryProfile) -> bool:
    """True once usage, a household/device count and budget are known.

    Commitment preference stays optional. The hard cap on questions is enforced by the caller
    through ``policy.yaml: limits.max_questions_advisory``; hitting it with something still
    unknown just means recommending with what is known.
    """
    if not profile.usage:
        return False
    has_size = profile.device_count is not None or profile.household_size is not None
    return has_size and profile.budget_gbp is not None
