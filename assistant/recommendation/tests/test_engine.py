import re
from pathlib import Path

import pytest

from core_common.config import clear_tenant_config_cache, load_tenant_config
from core_common.types import AdvisoryProfile, CommitmentPreference, UsageType
from recommendation.engine import recommend_packages

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config" / "tenants"

REQUIRED_ENV = {
    "MCP_CORE_URL": "http://mcp-core:8000/mcp",
    "MCP_PAYMENT_URL": "http://mcp-payment:8000/mcp",
    "MCP_TICKETING_URL": "http://mcp-ticketing:8000/mcp",
    "MCP_MONITORING_URL": "http://mcp-monitoring:8000/mcp",
    "MCP_NOTIFICATION_URL": "http://mcp-notification:8000/mcp",
}

# The 7 seeded packages (contracts §1.1), as the catalogue API would return them.
PACKAGES = [
    {
        "code": "FIBER_50_OGRENCI", "name": "Öğrenci Fiber 50", "down_mbps": 50, "up_mbps": 10,
        "commitment_months": 12, "monthly_price_try": 269.00, "target_profile": "student",
        "max_devices": 8, "static_ip": False, "tv_included": False, "gaming_optimized": False,
        "is_active": True,
    },
    {
        "code": "FIBER_100_TEMEL", "name": "Temel Fiber 100", "down_mbps": 100, "up_mbps": 20,
        "commitment_months": 24, "monthly_price_try": 349.00, "target_profile": "basic",
        "max_devices": 12, "static_ip": False, "tv_included": False, "gaming_optimized": False,
        "is_active": True,
    },
    {
        "code": "FIBER_200_AILE", "name": "Aile Fiber 200", "down_mbps": 200, "up_mbps": 40,
        "commitment_months": 24, "monthly_price_try": 459.00, "target_profile": "family",
        "max_devices": 20, "static_ip": False, "tv_included": True, "gaming_optimized": False,
        "is_active": True,
    },
    {
        "code": "FIBER_400_HOMEOFFICE", "name": "Home Office Fiber 400", "down_mbps": 400,
        "up_mbps": 80, "commitment_months": 24, "monthly_price_try": 629.00,
        "target_profile": "home_office", "max_devices": 30, "static_ip": True,
        "tv_included": False, "gaming_optimized": False, "is_active": True,
    },
    {
        "code": "FIBER_500_OYUNCU", "name": "Oyuncu Fiber 500", "down_mbps": 500, "up_mbps": 100,
        "commitment_months": 12, "monthly_price_try": 749.00, "target_profile": "gamer",
        "max_devices": 25, "static_ip": False, "tv_included": False, "gaming_optimized": True,
        "is_active": True,
    },
    {
        "code": "FIBER_1000_PREMIUM", "name": "Premium Fiber 1000", "down_mbps": 1000,
        "up_mbps": 200, "commitment_months": 24, "monthly_price_try": 999.00,
        "target_profile": "premium", "max_devices": 50, "static_ip": True,
        "tv_included": True, "gaming_optimized": True, "is_active": True,
    },
    {
        "code": "FIBER_200_ESNEK", "name": "Esnek Fiber 200 (taahhütsüz)", "down_mbps": 200,
        "up_mbps": 40, "commitment_months": 0, "monthly_price_try": 589.00,
        "target_profile": "basic", "max_devices": 20, "static_ip": False,
        "tv_included": False, "gaming_optimized": False, "is_active": True,
    },
]


@pytest.fixture()
def routing_config(monkeypatch: pytest.MonkeyPatch):
    clear_tenant_config_cache()
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    cfg = load_tenant_config("nethiz", config_dir=CONFIG_DIR)
    clear_tenant_config_cache()
    return cfg.routing


def _template_regex(template: str) -> re.Pattern[str]:
    """Turn a `{placeholder}`-style template into a regex matching any filled-in value."""
    parts = re.split(r"(\{[^}]+\})", template)
    pattern = "".join(r".+?" if part.startswith("{") else re.escape(part) for part in parts)
    return re.compile(f"^{pattern}$")


# -- 10 representative profiles -----------------------------------------------------------

PROFILES = {
    "student_on_a_budget": (
        AdvisoryProfile(
            usage=[UsageType.STUDENT], device_count=3, budget_try=300,
            commitment_preference=CommitmentPreference.TWELVE,
        ),
        "FIBER_50_OGRENCI",
    ),
    "large_family": (
        AdvisoryProfile(
            usage=[UsageType.FAMILY], household_size=6, device_count=15, budget_try=500,
            commitment_preference=CommitmentPreference.TWENTY_FOUR, needs_tv=True,
        ),
        "FIBER_200_AILE",
    ),
    "home_office_static_ip": (
        AdvisoryProfile(
            usage=[UsageType.HOME_OFFICE], device_count=10, budget_try=700,
            commitment_preference=CommitmentPreference.TWENTY_FOUR, needs_static_ip=True,
        ),
        "FIBER_400_HOMEOFFICE",
    ),
    "gamer": (
        AdvisoryProfile(
            usage=[UsageType.GAMING], device_count=5,
            commitment_preference=CommitmentPreference.TWELVE,
        ),
        "FIBER_500_OYUNCU",
    ),
    "no_commitment_seeker": (
        AdvisoryProfile(
            usage=[UsageType.BASIC], device_count=4, budget_try=600,
            commitment_preference=CommitmentPreference.NONE,
        ),
        "FIBER_200_ESNEK",
    ),
    "streaming_family": (
        AdvisoryProfile(
            usage=[UsageType.STREAMING], household_size=4, device_count=8, budget_try=500,
            commitment_preference=CommitmentPreference.TWENTY_FOUR, needs_tv=True,
        ),
        "FIBER_200_AILE",
    ),
    "light_user_small_budget": (
        AdvisoryProfile(
            usage=[UsageType.BASIC], device_count=2, budget_try=300,
            commitment_preference=CommitmentPreference.ANY,
        ),
        "FIBER_50_OGRENCI",
    ),
    "power_user_everything": (
        AdvisoryProfile(
            usage=[UsageType.GAMING, UsageType.STREAMING], device_count=20,
            commitment_preference=CommitmentPreference.TWENTY_FOUR,
            needs_static_ip=True, needs_tv=True,
        ),
        "FIBER_1000_PREMIUM",
    ),
    "minimal_tight_budget": (
        AdvisoryProfile(
            usage=[UsageType.BASIC], device_count=1, budget_try=280,
            commitment_preference=CommitmentPreference.NONE,
        ),
        "FIBER_50_OGRENCI",
    ),
    "gamer_strict_budget_falls_back_to_generic": (
        AdvisoryProfile(
            usage=[UsageType.GAMING], device_count=6, budget_try=400,
            commitment_preference=CommitmentPreference.TWELVE,
        ),
        "FIBER_100_TEMEL",
    ),
}


@pytest.mark.parametrize("name", list(PROFILES))
def test_profile_gets_a_sensible_best_package(routing_config, name):
    profile, expected_best = PROFILES[name]
    offers = recommend_packages(profile, PACKAGES, routing_config)

    assert offers, f"{name}: expected at least one offer"
    assert offers[0].is_best is True
    assert all(o.is_best is False for o in offers[1:])
    assert offers[0].package_code == expected_best, (
        f"{name}: expected {expected_best}, got {offers[0].package_code} "
        f"(scores={[(o.package_code, o.score) for o in offers]})"
    )


def test_top_3_at_most(routing_config):
    profile = AdvisoryProfile(usage=[UsageType.BASIC], device_count=2)
    offers = recommend_packages(profile, PACKAGES, routing_config)
    assert len(offers) <= 3


def test_budget_hard_filter_excludes_over_budget_packages(routing_config):
    profile = AdvisoryProfile(
        usage=[UsageType.GAMING], device_count=6, budget_try=400,
        commitment_preference=CommitmentPreference.TWELVE,
    )
    offers = recommend_packages(profile, PACKAGES, routing_config)
    codes = {o.package_code for o in offers}
    assert "FIBER_500_OYUNCU" not in codes  # 749 TL, way above 400 * 1.15
    assert "FIBER_1000_PREMIUM" not in codes  # 999 TL


def test_determinism_same_input_same_output(routing_config):
    profile = AdvisoryProfile(
        usage=[UsageType.FAMILY], household_size=4, device_count=8, budget_try=500,
        needs_tv=True, commitment_preference=CommitmentPreference.TWENTY_FOUR,
    )
    first = recommend_packages(profile, PACKAGES, routing_config)
    second = recommend_packages(profile, PACKAGES, routing_config)
    assert [o.model_dump() for o in first] == [o.model_dump() for o in second]


def test_every_reason_comes_from_configured_templates(routing_config):
    templates = routing_config.recommendation.reason_codes_tr
    patterns = [_template_regex(t) for t in templates.values()]

    for profile, _ in PROFILES.values():
        offers = recommend_packages(profile, PACKAGES, routing_config)
        for offer in offers:
            for reason in offer.reasons:
                assert any(p.match(reason) for p in patterns), f"unexpected free-text reason: {reason!r}"
