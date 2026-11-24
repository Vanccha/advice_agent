"""ADVISORY mode: ask a few fixed questions, then recommend packages deterministically.

Where this sits: entered from the router; called by `modes.dispatch` once per message.
Fills an `AdvisoryProfile`, then calls `recommendation.engine.recommend_packages` on the
live catalogue (via the gateway) and asks the LLM provider only to narrate the result.

Key ideas:
- The LLM never chooses, orders or prices a package: only the scoring engine does
  (contracts §4.3/§4.4). The model's wording is validated against the engine's offers
  (`_validate_model_reply`) and falls back to a fixed template on any mismatch or error.
- Answers are parsed into typed fields by a model reader plus offline regex fallbacks,
  and both go through one plausibility check, so the mode still works without an LLM.
- Bounded: at most ``policy.yaml: limits.max_questions_advisory`` questions, then it
  recommends with what it has.

Read next: recommendation/engine.py, modes/state.py (advisory fields), modes/replies.py.

Import as: ``from modes.advisory import handle_turn, AdvisoryStepResult``.
See: docs/contracts.md §4.3, §4.4.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from core_common.text import format_money
from core_common.types import AdvisoryProfile, CommitmentPreference, Mode, PackageOffer, StepType, UsageType
from llm.base import ChatMessage
from modes import replies
from modes.context import TurnContext
from modes.tool_data import as_list
from recommendation.engine import recommend_packages
from recommendation.questions import QUESTIONS, next_question, profile_is_complete

_USAGE_KEYWORDS: list[tuple[str, UsageType]] = [
    (r"student|stud(y|ies|ying)|school|universit|college|homework", UsageType.STUDENT),
    (r"famil|\bkids?\b|child", UsageType.FAMILY),
    (r"home.?office|office|remote|work(ing)? from home|\bwfh\b|\bwork", UsageType.HOME_OFFICE),
    (r"\bgam(e|es|ing|er|ers)\b|playstation|xbox|console", UsageType.GAMING),
    (r"stream|netflix|film|movie|series|\bshows?\b|watch", UsageType.STREAMING),
    (r"basic|simple|normal|everyday|browsing|e-?mail", UsageType.BASIC),
]

_COMMITMENT_KEYWORDS: list[tuple[str, CommitmentPreference]] = [
    (
        r"no.?contract|without (a |any )?contract|do(?:n'?t| not) want (a |any )?contract"
        r"|contract.?free|no (minimum term|commitment|tie)|rolling|month.?to.?month|\bnone\b",
        CommitmentPreference.NONE,
    ),
    (r"\b24\b|two.?years?", CommitmentPreference.TWENTY_FOUR),
    (r"\b12\b|one.?year|\ba year\b", CommitmentPreference.TWELVE),
    (
        r"no preference|do(?:n'?t| not) mind|does(?:n'?t| not) matter|not bothered|either|whichever"
        r"|whatever",
        CommitmentPreference.ANY,
    ),
]

_SKIP_WORDS_RE = re.compile(
    r"\bskip\b|\bpass\b|don'?t know|not sure|no idea|rather not|prefer not|\bnone\b", re.IGNORECASE
)
_INT_RE = re.compile(r"\d+")
_FLOAT_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


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


def _parse_usage(text: str, *, strict: bool = False) -> list[UsageType]:
    """`strict` means "say nothing rather than guess": used when we need to know whether the
    customer actually answered, instead of quietly defaulting them to BASIC."""
    found: list[UsageType] = []
    for pattern, usage in _USAGE_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE) and usage not in found:
            found.append(usage)
    if found:
        return found
    return [] if strict else [UsageType.BASIC]


def _parse_int(text: str) -> int | None:
    if _SKIP_WORDS_RE.search(text):
        return None
    match = _INT_RE.search(text)
    return int(match.group()) if match else None


def _parse_float(text: str) -> float | None:
    if _SKIP_WORDS_RE.search(text):
        return None
    match = _FLOAT_RE.search(text)
    if not match:
        return None
    return float(match.group().replace(",", ""))


def _parse_commitment(text: str, *, strict: bool = False) -> CommitmentPreference | None:
    for pattern, pref in _COMMITMENT_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return pref
    return None if strict else CommitmentPreference.ANY


# --- Understanding the answer -------------------------------------------------------
# Positional regex parsing misreads real sentences: "50 quid a month" given as the answer
# to "how many devices?" would set device_count=50, and "I can do a 24-month contract"
# would set a £24 budget. So the model may *read* the sentence into typed fields (it still
# never chooses a package); regex stays as the offline fallback, and both paths go through
# the same plausibility check.

_PLAUSIBLE = {
    "household_size": (1, 30),
    "device_count": (1, 200),
    "budget_gbp": (5.0, 10_000.0),
}


class _ExtractedFields(BaseModel):
    """Only what the sentence actually states; everything else stays None."""

    usage: list[str] | None = None
    household_size: int | None = None
    device_count: int | None = None
    budget_gbp: float | None = None
    commitment_preference: str | None = None
    needs_static_ip: bool | None = None
    needs_tv: bool | None = None


_EXTRACT_SYSTEM = (
    "You read one sentence from a broadband customer and extract only the facts it "
    "states. Never guess or infer a value that is not stated. Fields: usage (any of "
    "student, family, home_office, gaming, streaming, basic), household_size (people), "
    "device_count (connected devices), budget_gbp (monthly budget in pounds sterling), "
    "commitment_preference (none, 12, 24 or any), needs_static_ip, needs_tv. A sentence "
    "about a monthly budget is never a device count, and a commitment length in months is "
    "never a budget. Leave a field null when the sentence does not state it."
)


def _plausible(field_name: str, value: Any) -> bool:
    bounds = _PLAUSIBLE.get(field_name)
    if bounds is None or value is None:
        return value is not None
    low, high = bounds
    return low <= value <= high


def _extract_with_model(ctx: TurnContext, text: str) -> dict[str, Any]:
    """What the sentence states, as typed fields. Empty dict when the model cannot help."""
    try:
        extracted = ctx.provider.structured(
            system=_EXTRACT_SYSTEM,
            messages=[ChatMessage(role="user", content=text)],
            schema=_ExtractedFields,
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
        elif name in _PLAUSIBLE:
            if _plausible(name, value):
                values[name] = value
        else:
            values[name] = value
    return values


def _apply_answer(
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
    extracted = _extract_with_model(ctx, text) if ctx is not None else {}
    for name, value in extracted.items():
        data[name] = value
    if field_name in extracted:
        return AdvisoryProfile.model_validate(data), True
    # The model read the sentence and put its numbers somewhere else (a budget, a contract
    # length). Re-reading those same digits as the asked-for count or budget would repeat
    # the misreading the extraction exists to prevent, and a plausibility range cannot
    # catch it: "24" is both a believable contract length and a believable monthly budget.
    if extracted and field_name in _PLAUSIBLE:
        return AdvisoryProfile.model_validate(data), True

    skipped = bool(_SKIP_WORDS_RE.search(text))
    understood = bool(extracted) or skipped

    if field_name == "usage":
        parsed_usage = _parse_usage(text, strict=True)
        if parsed_usage:
            data["usage"] = [u.value for u in parsed_usage]
            understood = True
    elif field_name == "household_size":
        value = _parse_int(text)
        if _plausible("household_size", value):
            data["household_size"] = value
            understood = True
    elif field_name == "device_count":
        value = _parse_int(text)
        if _plausible("device_count", value):
            data["device_count"] = value
            understood = True
    elif field_name == "budget_gbp":
        value = _parse_float(text)
        if _plausible("budget_gbp", value):
            data["budget_gbp"] = value
            understood = True
    elif field_name == "commitment_preference":
        parsed_commitment = _parse_commitment(text, strict=True)
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
# The message that routed the customer here may already answer several questions ("four of
# us, we stream every evening, budget £50"). There is no "current question" yet, so the
# positional parsers above must not run (a bare "50" would become the device count): only
# the model's typed extraction and clearly labelled patterns are used.

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_NUM = r"(\d+|" + "|".join(_NUMBER_WORDS) + r")"
_OPENING_BUDGET_RES = [
    re.compile(r"£\s*(\d[\d,]*(?:\.\d+)?)"),
    re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:pounds?|quid|gbp)\b", re.IGNORECASE),
    re.compile(r"\bbudget\b\D{0,15}?(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE),
]
_OPENING_HOUSEHOLD_RES = [
    re.compile(rf"\b{_NUM}\s+of\s+us\b", re.IGNORECASE),
    re.compile(rf"\b{_NUM}\s+(?:people|persons|adults|members)\b", re.IGNORECASE),
    re.compile(rf"\b(?:family|household)\s+of\s+{_NUM}\b", re.IGNORECASE),
]
_OPENING_DEVICES_RE = re.compile(rf"\b{_NUM}\s+(?:connected\s+)?(?:devices|gadgets)\b", re.IGNORECASE)
_OPENING_COMMITMENT_RES: list[tuple[re.Pattern[str], CommitmentPreference]] = [
    (
        re.compile(
            r"no.?contract|without (?:a |any )?contract|contract.?free|no (?:minimum term|commitment)"
            r"|month.?to.?month",
            re.IGNORECASE,
        ),
        CommitmentPreference.NONE,
    ),
    (re.compile(r"\b24[- ]months?\b|two[- ]years?\b", re.IGNORECASE), CommitmentPreference.TWENTY_FOUR),
    (
        re.compile(r"\b12[- ]months?\b|one[- ]year\b|\b(?:a|1)[- ]year\b", re.IGNORECASE),
        CommitmentPreference.TWELVE,
    ),
]


def _to_number(raw: str) -> float:
    return float(_NUMBER_WORDS.get(raw.lower(), raw.replace(",", "")))


def _extract_opening_with_regex(text: str) -> dict[str, Any]:
    """Only clearly labelled statements; never a positional guess."""
    values: dict[str, Any] = {}
    for pattern in _OPENING_BUDGET_RES:
        match = pattern.search(text)
        if match and _plausible("budget_gbp", _to_number(match.group(1))):
            values["budget_gbp"] = _to_number(match.group(1))
            break
    for pattern in _OPENING_HOUSEHOLD_RES:
        match = pattern.search(text)
        if match and _plausible("household_size", _to_number(match.group(1))):
            values["household_size"] = int(_to_number(match.group(1)))
            break
    match = _OPENING_DEVICES_RE.search(text)
    if match and _plausible("device_count", _to_number(match.group(1))):
        values["device_count"] = int(_to_number(match.group(1)))
    for pattern, preference in _OPENING_COMMITMENT_RES:
        if pattern.search(text):
            values["commitment_preference"] = preference.value
            break
    usage = [u.value for u in _parse_usage(text, strict=True) if u is not UsageType.BASIC]
    if usage:
        values["usage"] = usage
    if re.search(r"stati[ck].?ip", text, re.IGNORECASE):
        values["needs_static_ip"] = True
    if re.search(r"\btv\b|television", text, re.IGNORECASE):
        values["needs_tv"] = True
    return values


def _apply_opening_message(
    profile: AdvisoryProfile, text: str, ctx: TurnContext
) -> tuple[AdvisoryProfile, list[str]]:
    """Fill every field the opening message states. Model extraction wins; labelled regex
    fills what the model did not return (and is the whole story with no model)."""
    values = _extract_opening_with_regex(text)
    values.update(_extract_with_model(ctx, text))
    data = profile.model_dump(mode="json")
    data.update(values)
    return AdvisoryProfile.model_validate(data), sorted(values)


def _format_offer(offer: PackageOffer) -> str:
    price = format_money(offer.monthly_price_gbp)
    lines = [f"• {offer.name} — {offer.down_mbps}/{offer.up_mbps} Mbps, {price}/month"]
    for reason in offer.reasons:
        lines.append(f"   - {reason}")
    return "\n".join(lines)


def _verbalize_offers(offers: list[PackageOffer]) -> str:
    if not offers:
        return replies.ADVISORY_NO_MATCH
    intro = replies.ADVISORY_INTRO
    body = "\n\n".join(_format_offer(offer) for offer in offers)
    best = offers[0]
    outro = replies.advisory_recommendation(best.name)
    return f"{intro}\n\n{body}{outro}"


_PRICE_MENTION_RE = re.compile(
    r"£\s*(\d[\d,]*(?:\.\d+)?)|(\d[\d,]*(?:\.\d+)?)\s*(?:GBP|pounds?)\b", re.IGNORECASE
)
_MBPS_MENTION_RE = re.compile(r"(\d+)\s*Mbps", re.IGNORECASE)


def _parse_gbp_amount(text: str) -> float:
    """``"1,234.50"`` / ``"26.90"`` / ``"27"`` -> float — undoes
    ``core_common.text.format_money``'s thousands separator so a model-stated amount can be
    compared numerically against an offer's own `monthly_price_gbp`, regardless of exactly
    how it chose to format it."""
    cleaned = text.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return float("nan")


def _offer_payload_for_model(offers: list[PackageOffer]) -> str:
    """Only the fields of the engine's own ``PackageOffer`` objects — the model is given
    nothing else it could use as material to invent a package, price or speed from."""
    lines = []
    for offer in offers:
        lines.append(
            f"- package_code={offer.package_code}; name={offer.name}; "
            f"down_mbps={offer.down_mbps}; up_mbps={offer.up_mbps}; "
            f"monthly_price_gbp={offer.monthly_price_gbp}; "
            f"commitment_months={offer.commitment_months}; is_best={offer.is_best}; "
            f"reasons={offer.reasons}"
        )
    return "\n".join(lines)


def _validate_model_reply(reply: str, offers: list[PackageOffer], catalog_names: set[str]) -> bool:
    """contracts §4.3: "The LLM may not invent or reorder packages." Deliberately
    conservative (reject on any doubt) — this is a safety net around a free-text reply, not
    a replacement for the deterministic engine, so every check here only accepts a reply
    that is directly verifiable against the `PackageOffer` data handed to the model."""
    if not reply or not reply.strip():
        return False

    offer_names = [offer.name for offer in offers]
    other_known_names = catalog_names - set(offer_names)
    if any(name in reply for name in other_known_names):
        return False  # named a real catalogue package the engine did not return

    mentioned_positions = [reply.find(name) for name in offer_names if name in reply]
    if not mentioned_positions:
        return False  # did not actually verbalize any of the offered packages
    if mentioned_positions != sorted(mentioned_positions):
        return False  # reordered the engine's own ranking

    valid_prices = {round(offer.monthly_price_gbp, 2) for offer in offers}
    for symbol_amount, word_amount in _PRICE_MENTION_RE.findall(reply):
        if round(_parse_gbp_amount(symbol_amount or word_amount), 2) not in valid_prices:
            return False  # stated a price that matches none of the offers

    valid_speeds = {offer.down_mbps for offer in offers} | {offer.up_mbps for offer in offers}
    for raw in _MBPS_MENTION_RE.findall(reply):
        if int(raw) not in valid_speeds:
            return False  # stated a speed that matches none of the offers

    return True


def _verbalize_with_model(
    ctx: TurnContext, offers: list[PackageOffer], all_packages: list[dict[str, Any]]
) -> str | None:
    """Ask the configured provider to narrate the engine's own offers. Returns
    `None` (never raises) when the provider errs or the reply fails `_validate_model_reply`
    — the caller then falls back to the deterministic template."""
    persona = ctx.tenant_config.persona
    system = (
        f"You are {persona.name_en}, a customer support assistant. Your tone: {persona.tone_en}. "
        "Present the package recommendations you are given (name, speed, price, contract, "
        "reasons) to the customer exactly as they are, in natural, concise British English. "
        "Mention ONLY the packages you are given: do not invent a package, do not change the "
        "order, do not change any price or speed, and do not promise anything about the "
        "packages that you were not given. Write prices in pounds with the £ sign."
    )
    user = (
        "Package recommendations to present to the customer (ranked by the engine, best first):\n"
        f"{_offer_payload_for_model(offers)}"
    )
    try:
        reply = ctx.provider.complete(
            system=system, messages=[ChatMessage(role="user", content=user)], temperature=0.3
        )
    except Exception:
        return None

    catalog_names = {p.get("name") for p in all_packages if p.get("name")}
    if not _validate_model_reply(reply, offers, catalog_names):
        return None
    return reply


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
        profile, filled = _apply_opening_message(profile, masked_message, ctx)
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
        profile, _understood = _apply_answer(profile, awaiting_field, masked_message, ctx)
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

    model_reply = _verbalize_with_model(ctx, offers, packages) if offers else None
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
        reply_en = _verbalize_offers(offers)
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
