"""Deterministic package scoring: which packages fit this customer, and why.

Where this sits: ``recommendation`` package. ``modes.advisory`` calls ``recommend_packages``
with the customer's ``AdvisoryProfile``, the live package catalogue (fetched through MCP) and
the tenant's ``RoutingFile``. The LLM never sees this code: it may only *narrate* the result.

Key ideas:
- Deterministic scoring, LLM only narrates: the same input always yields the same ranking,
  so advice is testable and explainable. Weights and wording come from ``routing.yaml``.
- Weighted components (speed, budget, profile, commitment, extras), each scoring 0..1; every
  component that fires also contributes a human-readable reason.
- Hard filters before ranking (over-budget, far-too-slow), but a filter steps aside rather
  than leave the customer with nothing.
- Stable tie-breaking (price, then commitment) so ordering never flickers.

Read next: ``recommendation/questions.py``, ``config/tenants/netswift/routing.yaml``.
See: docs/contracts.md §4.4.
"""
from __future__ import annotations

import math
from typing import Any

from core_common.config_models import RoutingFile
from core_common.text import format_money
from core_common.types import AdvisoryProfile, CommitmentPreference, PackageOffer, UsageType

# Customer-facing label for each catalogue `target_profile` value (contracts §1.1). The
# `profile_match` template in routing.yaml has a `{profile_en}` placeholder but no lookup for
# it, so the vocabulary lives here (a fixed set of words, never free text).
_TARGET_PROFILE_EN = {
    "student": "student",
    "family": "family",
    "home_office": "home office",
    "gamer": "gaming",
    "basic": "everyday",
    "premium": "premium",
    "small_business": "small business",
}


def _devices_for_sizing(profile: AdvisoryProfile, devices_per_person: float) -> tuple[int, bool]:
    """Return (device count to size the line for, whether it is an estimate).

    The customer's own device count wins; otherwise estimate from household size; 1 only
    when neither is known. The flag lets the reply say "household of N" instead of
    pretending the customer gave a device count.
    """
    if profile.device_count:
        return profile.device_count, False
    if profile.household_size:
        return max(1, math.ceil(profile.household_size * devices_per_person)), True
    return 1, False


def _speed_component(
    profile: AdvisoryProfile, package: dict[str, Any], rec_cfg: Any, templates: dict[str, str]
) -> tuple[float, str | None]:
    """Speed adequacy: download speed relative to what the household's devices need."""
    device_count, estimated = _devices_for_sizing(profile, rec_cfg.devices_per_person)
    required_mbps = max(rec_cfg.min_mbps_floor, device_count * rec_cfg.mbps_per_device)
    down_mbps = package["down_mbps"]
    score = min(1.0, down_mbps / required_mbps) if required_mbps else 1.0
    key = "speed_ok" if down_mbps >= required_mbps else "speed_tight"
    # An estimate must never be presented as a count the customer gave: say "household of N".
    if estimated and f"{key}_household" in templates:
        key = f"{key}_household"
    reason = templates[key].format(
        device_count=device_count,
        devices_en=f"{device_count} device" + ("" if device_count == 1 else "s"),
        household_size=profile.household_size,
        down_mbps=down_mbps,
    )
    return score, reason


def _budget_component(
    profile: AdvisoryProfile, package: dict[str, Any], tolerance: float, templates: dict[str, str]
) -> tuple[float, str | None, bool]:
    """Budget fit; returns (score, reason, excluded).

    `excluded` is the hard filter: above budget plus the tolerance band, never offered.
    """
    budget = profile.budget_gbp
    price = package["monthly_price_gbp"]
    if budget is None:
        return 1.0, None, False
    if price > budget * (1 + tolerance):
        return 0.0, None, True
    if price <= budget:
        # Graded, not binary (1.0 down to 0.5): otherwise `budget_fit`'s weight has nothing
        # to act on and a tenant that wants price-first advice could not express it.
        score = 1.0 - 0.5 * (price / budget)
        return score, templates["budget_ok"].format(monthly_price_gbp=format_money(price)), False
    # over budget but within the 15% tolerance band
    overage_ratio = (price - budget) / (budget * tolerance) if tolerance else 1.0
    score = max(0.0, 1.0 - overage_ratio)
    return score, templates["budget_over"].format(monthly_price_gbp=format_money(price)), False


def _profile_component(
    profile: AdvisoryProfile, package: dict[str, Any], usage_profile_map: dict[str, list[str]], templates: dict[str, str]
) -> tuple[float, str | None]:
    """Profile match: does the package's target audience fit any stated usage?"""
    target_profile = package.get("target_profile")
    matches = any(target_profile in usage_profile_map.get(usage.value, []) for usage in profile.usage)
    if not matches or target_profile is None:
        return 0.0, None
    profile_en = _TARGET_PROFILE_EN.get(target_profile, target_profile)
    return 1.0, templates["profile_match"].format(profile_en=profile_en)


def _commitment_component(
    profile: AdvisoryProfile, package: dict[str, Any], templates: dict[str, str]
) -> tuple[float, str | None]:
    """Commitment match: exact wish scores 1.0; a shorter contract beats a longer one."""
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
    """Extras match: share of requested extras (static IP, TV, gaming) the package has."""
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
    """Rank active packages for the profile and return the top three offers.

    Pure and deterministic: same input, same output. `packages` are catalogue rows as dicts;
    the first offer is flagged `is_best`.
    """
    rec_cfg = routing_config.recommendation
    weights = rec_cfg.weights
    templates = rec_cfg.reason_codes_en

    device_count, _estimated = _devices_for_sizing(profile, rec_cfg.devices_per_person)
    required_mbps = max(rec_cfg.min_mbps_floor, device_count * rec_cfg.mbps_per_device)
    speed_cutoff = required_mbps * rec_cfg.speed_hard_filter_ratio

    scored: list[tuple[float, dict[str, Any], list[str]]] = []

    for package in packages:
        if package.get("is_active", True) is False:
            continue

        budget_score, budget_reason, excluded = _budget_component(
            profile, package, rec_cfg.budget_hard_filter_tolerance, templates
        )
        if excluded:
            continue

        speed_score, speed_reason = _speed_component(profile, package, rec_cfg, templates)
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

    # Rule: never offer a package that cannot plausibly serve the household (a slow line for
    # many streaming devices is bad advice even as a cheap third option). The filter steps
    # aside when it would leave nothing, so the customer still sees the closest package
    # with an honest "tight" reason.
    adequate = [item for item in scored if item[1]["down_mbps"] >= speed_cutoff]
    if adequate:
        scored = adequate

    # Highest score first; ties -> lower price, then shorter commitment.
    scored.sort(key=lambda item: (-item[0], item[1]["monthly_price_gbp"], item[1]["commitment_months"]))

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
                monthly_price_gbp=package["monthly_price_gbp"],
                score=score,
                reasons=reasons,
                is_best=(index == 0),
            )
        )
    return offers
