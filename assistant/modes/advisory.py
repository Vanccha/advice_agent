"""ADVISORY mode (contracts §4.3/§4.4): ask at most
``policy.yaml: limits.max_questions_advisory`` (5) fixed Turkish questions, fill an
``AdvisoryProfile``, then call the **deterministic** ``recommend_packages`` — the LLM never
invents, reorders or re-prices a package; only ``recommendation.engine`` decides that.

Parsing a free-text answer into a typed ``AdvisoryProfile`` field is done with small
deterministic heuristics (keyword/number extraction), not a model call: this keeps the
whole mode runnable offline (no LLM dependency) and perfectly reproducible — the same
answer always fills the same field the same way, which is what the "same profile twice
gives the same packages" test asserts transitively.

The verbalisation step asks the configured provider to narrate the engine's own
``PackageOffer`` objects in Turkish (contracts §4.3: "the LLM only verbalizes the result").
The model is handed nothing but those offers (name/code/speed/price/commitment/reasons) and
its reply is validated before use (``_validate_model_reply``): it may only name packages the
engine actually returned, in the engine's own order, and every price/speed figure it states
must match the offer data exactly. A provider error or a reply that fails validation falls
back to the fixed Turkish template (``_verbalize_offers``), which is built only from the
`PackageOffer` fields and therefore trivially satisfies the same constraint. Either way, the
*packages themselves* (`AdvisoryStepResult.offers`) always come straight from
``recommend_packages`` — the LLM's wording can never change what was actually recommended.

Import as: ``from modes.advisory import handle_turn, AdvisoryStepResult``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core_common.tr import format_money_try
from core_common.types import AdvisoryProfile, CommitmentPreference, Mode, PackageOffer, StepType, UsageType
from llm.base import ChatMessage
from modes.context import TurnContext
from modes.tool_data import as_list
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


_PRICE_MENTION_RE = re.compile(r"(\d[\d.,]*)\s*TL", re.IGNORECASE)
_MBPS_MENTION_RE = re.compile(r"(\d+)\s*Mbps", re.IGNORECASE)


def _parse_try_amount(text: str) -> float:
    """``"1.234,50"`` / ``"269,00"`` / ``"269"`` -> float — undoes
    ``core_common.tr.format_money_try``'s Turkish separators so a model-stated amount can be
    compared numerically against an offer's own `monthly_price_try`, regardless of exactly
    how it chose to format it."""
    cleaned = text.replace(".", "").replace(",", ".")
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
            f"monthly_price_try={offer.monthly_price_try}; "
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

    valid_prices = {round(offer.monthly_price_try, 2) for offer in offers}
    for raw in _PRICE_MENTION_RE.findall(reply):
        if round(_parse_try_amount(raw), 2) not in valid_prices:
            return False  # stated a price that matches none of the offers

    valid_speeds = {offer.down_mbps for offer in offers} | {offer.up_mbps for offer in offers}
    for raw in _MBPS_MENTION_RE.findall(reply):
        if int(raw) not in valid_speeds:
            return False  # stated a speed that matches none of the offers

    return True


def _verbalize_with_model(
    ctx: TurnContext, offers: list[PackageOffer], all_packages: list[dict[str, Any]]
) -> str | None:
    """Ask the configured provider to narrate the engine's own offers in Turkish. Returns
    `None` (never raises) when the provider errs or the reply fails `_validate_model_reply`
    — the caller then falls back to the deterministic template."""
    persona = ctx.tenant_config.persona
    system = (
        f"Sen {persona.name_tr} adlı bir müşteri destek asistanısın. Üslubun: {persona.tone_tr}. "
        "Sana verilen paket önerilerini (ad, hız, fiyat, taahhüt, nedenler) olduğu gibi, "
        "doğal ve kısa bir Türkçe ile müşteriye anlat. SADECE sana verilen paketlerden "
        "bahset: yeni bir paket uydurma, verilen sırayı değiştirme, fiyat ya da hız "
        "bilgisini değiştirme, ya da paketlerle ilgili verilmeyen bir söz verme."
    )
    user = (
        "Müşteriye anlatılacak paket önerileri (motor tarafından sıralandı, ilk en iyisi):\n"
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
        reply_tr = model_reply
        ctx.audit_log.append(
            ctx.conversation_id,
            StepType.MODE_DECISION,
            "advisory: model verbalized the engine's offers",
            "reply validated against PackageOffer names/order/price/speed before use",
            {"offer_codes": [o.package_code for o in offers]},
            tenant=ctx.tenant,
        )
    else:
        reply_tr = _verbalize_offers(offers)
        ctx.audit_log.append(
            ctx.conversation_id,
            StepType.MODE_DECISION,
            "advisory: used deterministic verbalization template",
            (
                "no packages to recommend"
                if not offers
                else "model reply missing/invalid or provider error; fixed Turkish template used instead"
            ),
            {"offer_codes": [o.package_code for o in offers]},
            tenant=ctx.tenant,
        )

    if offers and not complete and questions_asked >= max_questions:
        reply_tr += (
            "\n\n(Sorabileceğim soru sayısına ulaştığım için elimdeki bilgilerle önerdim.)"
        )

    return AdvisoryStepResult(
        reply_tr=reply_tr,
        done=True,
        profile=profile,
        awaiting_field=None,
        questions_asked=questions_asked,
        offers=[o.model_dump(mode="json") for o in offers],
    )
