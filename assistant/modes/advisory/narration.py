"""Putting the engine's package offers into words, and checking the model's wording.

Where this sits: `modes.advisory`, last step. `flow.py` asks `verbalize_with_model` first and
falls back to the fixed template `verbalize_offers` when the model errs or is not trusted.

Key ideas:
- The LLM never chooses, orders or prices a package (contracts §4.3). Its text is only
  accepted if it is verifiable against the engine's own `PackageOffer` objects.
- The validator is deliberately conservative: reject on any doubt, because the fallback
  template is always available and always correct.

Read next: recommendation/engine.py, modes/advisory/flow.py.
See: docs/contracts.md §4.3, §4.4.
"""
from __future__ import annotations

import re
from typing import Any

from core_common.text import format_money
from core_common.types import PackageOffer
from llm.base import ChatMessage
from modes import replies
from modes.context import TurnContext

# --- Deterministic template ---------------------------------------------------------
def _format_offer(offer: PackageOffer) -> str:
    price = format_money(offer.monthly_price_gbp)
    lines = [f"• {offer.name} — {offer.down_mbps}/{offer.up_mbps} Mbps, {price}/month"]
    for reason in offer.reasons:
        lines.append(f"   - {reason}")
    return "\n".join(lines)


def verbalize_offers(offers: list[PackageOffer]) -> str:
    if not offers:
        return replies.ADVISORY_NO_MATCH
    intro = replies.ADVISORY_INTRO
    body = "\n\n".join(_format_offer(offer) for offer in offers)
    best = offers[0]
    outro = replies.advisory_recommendation(best.name)
    return f"{intro}\n\n{body}{outro}"


# --- Model narration and its validation ---------------------------------------------
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


def validate_model_reply(reply: str, offers: list[PackageOffer], catalog_names: set[str]) -> bool:
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


def verbalize_with_model(
    ctx: TurnContext, offers: list[PackageOffer], all_packages: list[dict[str, Any]]
) -> str | None:
    """Ask the configured provider to narrate the engine's own offers. Returns
    `None` (never raises) when the provider errs or the reply fails `validate_model_reply`
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
    if not validate_model_reply(reply, offers, catalog_names):
        return None
    return reply
