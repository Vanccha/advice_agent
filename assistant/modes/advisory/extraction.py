"""Reading a customer's sentence into typed advisory fields: model first, regex as fallback.

Where this sits: `modes.advisory`, middle layer. `flow.py` calls `apply_opening_message` for
the message that opened the conversation and `apply_answer` for every later answer.

Key ideas:
- The model may *read* a sentence into typed fields; it still never chooses a package.
- Anything the model returns is re-checked (enum membership, plausibility ranges), and the
  regex parsers in `parsing.py` keep the mode working with no model at all.
- `apply_answer` reports whether the sentence was understood, so the questionnaire can ask
  again instead of marching on as if a non-answer were an answer.

Read next: modes/advisory/parsing.py, modes/advisory/flow.py.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from core_common.types import AdvisoryProfile, CommitmentPreference, UsageType
from llm.base import ChatMessage
from modes.advisory.parsing import (
    PLAUSIBLE,
    SKIP_WORDS_RE,
    extract_opening_with_regex,
    parse_commitment,
    parse_float,
    parse_int,
    parse_usage,
    plausible,
)
from modes.context import TurnContext

# --- Model extraction ---------------------------------------------------------------
# Positional regex parsing misreads real sentences, so the model reads the sentence into
# typed fields; regex stays as the offline fallback and both go through `plausible`.

class ExtractedFields(BaseModel):
    """Only what the sentence actually states; everything else stays None."""

    usage: list[str] | None = None
    household_size: int | None = None
    device_count: int | None = None
    budget_gbp: float | None = None
    commitment_preference: str | None = None
    needs_static_ip: bool | None = None
    needs_tv: bool | None = None


EXTRACT_SYSTEM = (
    "You read one sentence from a broadband customer and extract only the facts it "
    "states. Never guess or infer a value that is not stated. Fields: usage (any of "
    "student, family, home_office, gaming, streaming, basic), household_size (people), "
    "device_count (connected devices), budget_gbp (monthly budget in pounds sterling), "
    "commitment_preference (none, 12, 24 or any), needs_static_ip, needs_tv. A sentence "
    "about a monthly budget is never a device count, and a commitment length in months is "
    "never a budget. Leave a field null when the sentence does not state it."
)


def extract_with_model(ctx: TurnContext, text: str) -> dict[str, Any]:
    """What the sentence states, as typed fields. Empty dict when the model cannot help."""
    try:
        extracted = ctx.provider.structured(
            system=EXTRACT_SYSTEM,
            messages=[ChatMessage(role="user", content=text)],
            schema=ExtractedFields,
        )
    except Exception:  # provider unavailable or answered unusably — fall back to regex
        return {}

    values: dict[str, Any] = {}
    for name, value in extracted.model_dump().items():
        if value is None:
            continue
        if name == "usage":
            usages = [u for u in value if u in {m.value for m in UsageType}]
            if usages:
                values["usage"] = usages
        elif name == "commitment_preference":
            if value in {m.value for m in CommitmentPreference}:
                values[name] = value
        elif name in PLAUSIBLE:
            if plausible(name, value):
                values[name] = value
        else:
            values[name] = value
    return values


def apply_answer(
    profile: AdvisoryProfile,
    field_name: str,
    text: str,
    ctx: TurnContext | None = None,
) -> tuple[AdvisoryProfile, bool]:
    """Returns `(profile, understood)`. `understood` is False when the sentence said nothing
    about the question asked — "I had fish and chips" is not a budget, and marching on to the next
    question as if it were is how a questionnaire feels broken."""
    data = profile.model_dump(mode="json")

    # Whatever the sentence genuinely states, for any field — a customer who answers
    # "there are 4 of us and 8 devices" should not be asked about devices again.
    extracted = extract_with_model(ctx, text) if ctx is not None else {}
    for name, value in extracted.items():
        data[name] = value
    if field_name in extracted:
        return AdvisoryProfile.model_validate(data), True
    # The model read the sentence and put its numbers somewhere else (a budget, a contract
    # length). Re-reading those same digits as the asked-for count or budget would repeat
    # the misreading the extraction exists to prevent, and a plausibility range cannot
    # catch it: "24" is both a believable contract length and a believable monthly budget.
    if extracted and field_name in PLAUSIBLE:
        return AdvisoryProfile.model_validate(data), True

    skipped = bool(SKIP_WORDS_RE.search(text))
    understood = bool(extracted) or skipped

    if field_name == "usage":
        parsed_usage = parse_usage(text, strict=True)
        if parsed_usage:
            data["usage"] = [u.value for u in parsed_usage]
            understood = True
    elif field_name == "household_size":
        value = parse_int(text)
        if plausible("household_size", value):
            data["household_size"] = value
            understood = True
    elif field_name == "device_count":
        value = parse_int(text)
        if plausible("device_count", value):
            data["device_count"] = value
            understood = True
    elif field_name == "budget_gbp":
        value = parse_float(text)
        if plausible("budget_gbp", value):
            data["budget_gbp"] = value
            understood = True
    elif field_name == "commitment_preference":
        parsed_commitment = parse_commitment(text, strict=True)
        if parsed_commitment is not None:
            data["commitment_preference"] = parsed_commitment.value
            understood = True
    needs_static_ip = bool(re.search(r"stati[ck].?ip", text, re.IGNORECASE))
    needs_tv = bool(re.search(r"\btv\b|television", text, re.IGNORECASE))
    if needs_static_ip:
        data["needs_static_ip"] = True
    if needs_tv:
        data["needs_tv"] = True
    if needs_static_ip or needs_tv:
        understood = True
    return AdvisoryProfile.model_validate(data), understood



# --- The opening message ------------------------------------------------------------

def apply_opening_message(
    profile: AdvisoryProfile, text: str, ctx: TurnContext
) -> tuple[AdvisoryProfile, list[str]]:
    """Fill every field the opening message states. Model extraction wins; labelled regex
    fills what the model did not return (and is the whole story with no model)."""
    values = extract_opening_with_regex(text)
    values.update(extract_with_model(ctx, text))
    data = profile.model_dump(mode="json")
    data.update(values)
    return AdvisoryProfile.model_validate(data), sorted(values)
