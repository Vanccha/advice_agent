"""ADVISORY mode (contracts §4.3/§4.4): ask at most
``policy.yaml: limits.max_questions_advisory`` (5) fixed Turkish questions, fill an
``AdvisoryProfile``, then call the **deterministic** ``recommend_packages`` — the LLM (or,
here, a fixed Turkish template — see the module docstring below) never invents, reorders
or re-prices a package; only ``recommendation.engine`` decides that.

Parsing a free-text answer into a typed ``AdvisoryProfile`` field is done with small
deterministic heuristics (keyword/number extraction), not a model call: this keeps the
whole mode runnable offline (no LLM dependency) and perfectly reproducible — the same
answer always fills the same field the same way, which is what the "same profile twice
gives the same packages" test asserts transitively. The verbalisation step is likewise a
fixed Turkish template built only from the fields of the ``PackageOffer`` objects
``recommend_packages`` returns, which trivially satisfies "the reply only names
engine-returned packages": there is no generative step that could invent one.

Import as: ``from modes.advisory import handle_turn, AdvisoryStepResult``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core_common.tr import format_money_try
from core_common.types import AdvisoryProfile, CommitmentPreference, Mode, PackageOffer, StepType, UsageType
from modes.context import TurnContext
from recommendation.engine import recommend_packages
from recommendation.questions import QUESTIONS, next_question, profile_is_complete

_USAGE_KEYWORDS: list[tuple[str, UsageType]] = [
    (r"öğrenc|ogrenc|okul", UsageType.STUDENT),
    (r"aile|ev.?içi|cocuk|çocuk", UsageType.FAMILY),
    (r"ev ofis|ofis|uzaktan|home.?office|çalış", UsageType.HOME_OFFICE),
    (r"oyun|game|gaming", UsageType.GAMING),
    (r"dizi|film|netflix|izle|stream", UsageType.STREAMING),
    (r"temel|basit|normal", UsageType.BASIC),
]

_COMMITMENT_KEYWORDS: list[tuple[str, CommitmentPreference]] = [
    (r"taahhütsüz|taahhutsuz|yok\b", CommitmentPreference.NONE),
    (r"\b24\b", CommitmentPreference.TWENTY_FOUR),
    (r"\b12\b", CommitmentPreference.TWELVE),
    (r"fark.?etmez|farketmez|önemli değil|onemli degil", CommitmentPreference.ANY),
]

_SKIP_WORDS_RE = re.compile(r"geç|gecebil|bilmiyor|yok\b|önemli değil|belirtmek istemiyorum", re.IGNORECASE)
_INT_RE = re.compile(r"\d+")
_FLOAT_RE = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass
class AdvisoryStepResult:
    reply_tr: str
    done: bool
    profile: AdvisoryProfile
    awaiting_field: str | None
    questions_asked: int
    offers: list[dict[str, Any]] = field(default_factory=list)


def _parse_usage(text: str) -> list[UsageType]:
    found: list[UsageType] = []
    for pattern, usage in _USAGE_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE) and usage not in found:
            found.append(usage)
    return found or [UsageType.BASIC]


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
    return float(match.group().replace(",", "."))


def _parse_commitment(text: str) -> CommitmentPreference:
    for pattern, pref in _COMMITMENT_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return pref
    return CommitmentPreference.ANY


def _apply_answer(profile: AdvisoryProfile, field_name: str, text: str) -> AdvisoryProfile:
    data = profile.model_dump(mode="json")
    if field_name == "usage":
        data["usage"] = [u.value for u in _parse_usage(text)]
    elif field_name == "household_size":
        value = _parse_int(text)
        data["household_size"] = value if value is not None else 1
    elif field_name == "device_count":
        value = _parse_int(text)
        data["device_count"] = value if value is not None else 1
    elif field_name == "budget_try":
        data["budget_try"] = _parse_float(text)
    elif field_name == "commitment_preference":
        data["commitment_preference"] = _parse_commitment(text).value
    needs_static_ip = bool(re.search(r"statik.?ip|static.?ip", text, re.IGNORECASE))
    needs_tv = bool(re.search(r"\btv\b|televizyon", text, re.IGNORECASE))
    if needs_static_ip:
        data["needs_static_ip"] = True
    if needs_tv:
        data["needs_tv"] = True
    return AdvisoryProfile.model_validate(data)


def _format_offer(offer: PackageOffer) -> str:
    price = format_money_try(offer.monthly_price_try)
    lines = [f"• {offer.name} — {offer.down_mbps}/{offer.up_mbps} Mbps, {price}/ay"]
    for reason in offer.reasons:
        lines.append(f"   - {reason}")
    return "\n".join(lines)


def _verbalize_offers(offers: list[PackageOffer]) -> str:
    if not offers:
        return (
            "Verdiğiniz bilgilere uygun bir paket bulamadım. Bütçenizi veya ihtiyaçlarınızı "
            "biraz esnetmek ister misiniz?"
        )
    intro = "İhtiyaçlarınıza göre en uygun paketleri buldum:"
    body = "\n\n".join(_format_offer(offer) for offer in offers)
    best = offers[0]
    outro = f"\n\nÖnerim: {best.name}."
    return f"{intro}\n\n{body}{outro}"


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

    if not is_first_turn and awaiting_field and masked_message is not None:
        profile = _apply_answer(profile, awaiting_field, masked_message)
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
            reply_tr=question.question_tr,
            done=False,
            profile=profile,
            awaiting_field=question.field,
            questions_asked=questions_asked,
        )

    # All done (or question budget spent): fetch the live catalogue and recommend.
    catalog_outcome = ctx.call_tool("list_packages", {})
    packages_raw = catalog_outcome.data if getattr(catalog_outcome, "ok", False) else None
    packages = _extract_packages(packages_raw)

    offers = recommend_packages(profile, packages, ctx.tenant_config.routing)
    ctx.audit_log.append(
        ctx.conversation_id,
        StepType.ACTION,
        "recommend_packages produced package offers",
        "deterministic scoring, no LLM package choice",
        {"offer_codes": [o.package_code for o in offers], "scores": [o.score for o in offers]},
        tenant=ctx.tenant,
    )
    reply_tr = _verbalize_offers(offers)

    return AdvisoryStepResult(
        reply_tr=reply_tr,
        done=True,
        profile=profile,
        awaiting_field=None,
        questions_asked=questions_asked,
        offers=[o.model_dump(mode="json") for o in offers],
    )


def _extract_packages(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        if "items" in raw and isinstance(raw["items"], list):
            return raw["items"]
        if "packages" in raw and isinstance(raw["packages"], list):
            return raw["packages"]
        # a single package dict, unlikely but handled defensively
        if "code" in raw:
            return [raw]
        return []
    if isinstance(raw, list):
        return raw
    return []
