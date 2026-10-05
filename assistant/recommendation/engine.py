"""Deterministic package scoring — no LLM anywhere in this module (contracts §4.4).

Import as: ``from recommendation.engine import recommend_packages``.
"""
from __future__ import annotations

from typing import Any

from core_common.config import RoutingFile
from core_common.tr import format_money_try
from core_common.types import AdvisoryProfile, CommitmentPreference, PackageOffer, UsageType

# Turkish label for each catalogue `target_profile` value (contracts §1.1 packages table).
# routing.yaml's `profile_match` reason template expects a `{profile_tr}` placeholder but
# does not itself supply this lookup, so it is kept here as the minimal piece of Turkish
# vocabulary needed to fill an otherwise-templated string (never free text).
_TARGET_PROFILE_TR = {
    "student": "Öğrenci",
    "family": "Aile",
    "home_office": "Ev Ofisi",
    "gamer": "Oyuncu",
    "basic": "Temel",
    "premium": "Premium",
    "small_business": "Küçük İşletme",
}


def _money(amount: float) -> str:
    return format_money_try(amount).removesuffix(" TL")


def _speed_component(
    profile: AdvisoryProfile, package: dict[str, Any], mbps_per_device: float, min_mbps_floor: float, templates: dict[str, str]
) -> tuple[float, str | None]:
    device_count = profile.device_count or 1
    required_mbps = max(min_mbps_floor, device_count * mbps_per_device)
    down_mbps = package["down_mbps"]
    score = min(1.0, down_mbps / required_mbps) if required_mbps else 1.0
    if down_mbps >= required_mbps:
        reason = templates["speed_ok"].format(device_count=device_count, down_mbps=down_mbps)
    else:
        reason = templates["speed_tight"].format(device_count=device_count, down_mbps=down_mbps)
    return score, reason


def _budget_component(
    profile: AdvisoryProfile, package: dict[str, Any], tolerance: float, templates: dict[str, str]
) -> tuple[float, str | None, bool]:
    """Returns (score, reason, excluded)."""
    budget = profile.budget_try
    price = package["monthly_price_try"]
    if budget is None:
        return 1.0, None, False
    if price > budget * (1 + tolerance):
        return 0.0, None, True
    if price <= budget:
        return 1.0, templates["budget_ok"].format(monthly_price_try=_money(price)), False
    # over budget but within the 15% tolerance band
    overage_ratio = (price - budget) / (budget * tolerance) if tolerance else 1.0
    score = max(0.0, 1.0 - overage_ratio)
    return score, templates["budget_over"].format(monthly_price_try=_money(price)), False


def _profile_component(
    profile: AdvisoryProfile, package: dict[str, Any], usage_profile_map: dict[str, list[str]], templates: dict[str, str]
) -> tuple[float, str | None]:
    target_profile = package.get("target_profile")
    matches = any(target_profile in usage_profile_map.get(usage.value, []) for usage in profile.usage)
    if not matches or target_profile is None:
        return 0.0, None
    profile_tr = _TARGET_PROFILE_TR.get(target_profile, target_profile)
    return 1.0, templates["profile_match"].format(profile_tr=profile_tr)


def _commitment_component(
    profile: AdvisoryProfile, package: dict[str, Any], templates: dict[str, str]
) -> tuple[float, str | None]:
    commitment_months = package["commitment_months"]
    preference = profile.commitment_preference

    if preference is None or preference == CommitmentPreference.ANY:
        if commitment_months == 0:
            return 1.0, templates["no_commitment"]
        return 1.0, templates["commitment_discount"].format(commitment_months=commitment_months)

    if preference == CommitmentPreference.NONE:
        if commitment_months == 0:
            return 1.0, templates["no_commitment"]
        return 0.3, None

    wanted = int(preference.value)
    if commitment_months == wanted:
        if commitment_months == 0:
            return 1.0, templates["no_commitment"]
        return 1.0, templates["commitment_discount"].format(commitment_months=commitment_months)
    if commitment_months == 0:
        return 0.6, templates["no_commitment"]
    return 0.5, None


def _extras_component(
    profile: AdvisoryProfile, package: dict[str, Any], templates: dict[str, str]
) -> tuple[float, list[str]]:
    requested: list[str] = []
    if profile.needs_static_ip:
        requested.append("static_ip")
    if profile.needs_tv:
        requested.append("tv_included")
    if UsageType.GAMING in profile.usage:
        requested.append("gaming_optimized")

    if not requested:
        return 1.0, []

    satisfied_flags = {
        "static_ip": bool(package.get("static_ip")),
        "tv_included": bool(package.get("tv_included")),
        "gaming_optimized": bool(package.get("gaming_optimized")),
    }
    reasons = [templates[key] for key in requested if satisfied_flags[key]]
    score = sum(1 for key in requested if satisfied_flags[key]) / len(requested)
    return score, reasons


def recommend_packages(
    profile: AdvisoryProfile, packages: list[dict[str, Any]], routing_config: RoutingFile
) -> list[PackageOffer]:
    """Pure, deterministic. Same input always produces the same output."""
    rec_cfg = routing_config.recommendation
    weights = rec_cfg.weights
    templates = rec_cfg.reason_codes_tr

    scored: list[tuple[float, dict[str, Any], list[str]]] = []

    for package in packages:
        if package.get("is_active", True) is False:
            continue

        budget_score, budget_reason, excluded = _budget_component(
            profile, package, rec_cfg.budget_hard_filter_tolerance, templates
        )
        if excluded:
            continue

        speed_score, speed_reason = _speed_component(
            profile, package, rec_cfg.mbps_per_device, rec_cfg.min_mbps_floor, templates
        )
        profile_score, profile_reason = _profile_component(
            profile, package, rec_cfg.usage_profile_map, templates
        )
        commitment_score, commitment_reason = _commitment_component(profile, package, templates)
        extras_score, extras_reasons = _extras_component(profile, package, templates)

        total = (
            weights.speed_adequacy * speed_score
            + weights.budget_fit * budget_score
            + weights.profile_match * profile_score
            + weights.commitment_match * commitment_score
            + weights.extras_match * extras_score
        )

        reasons = [r for r in (speed_reason, budget_reason, profile_reason, commitment_reason) if r]
        reasons.extend(extras_reasons)

        scored.append((round(total, 6), package, reasons))

    # Highest score first; ties -> lower price, then shorter commitment.
    scored.sort(key=lambda item: (-item[0], item[1]["monthly_price_try"], item[1]["commitment_months"]))

    top = scored[:3]
    offers: list[PackageOffer] = []
    for index, (score, package, reasons) in enumerate(top):
        offers.append(
            PackageOffer(
                package_code=package["code"],
                name=package["name"],
                down_mbps=package["down_mbps"],
                up_mbps=package["up_mbps"],
                commitment_months=package["commitment_months"],
                monthly_price_try=package["monthly_price_try"],
                score=score,
                reasons=reasons,
                is_best=(index == 0),
            )
        )
    return offers
