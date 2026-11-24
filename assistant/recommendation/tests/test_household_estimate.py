"""Device count estimated from household size when the customer did not state one."""
from __future__ import annotations

import pytest

from core_common.config import clear_tenant_config_cache, load_tenant_config
from core_common.types import AdvisoryProfile, UsageType
from recommendation.engine import recommend_packages
from recommendation.tests.test_engine import CONFIG_DIR, PACKAGES, REQUIRED_ENV


@pytest.fixture()
def routing(monkeypatch: pytest.MonkeyPatch):
    clear_tenant_config_cache()
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    cfg = load_tenant_config("netswift", config_dir=CONFIG_DIR)
    clear_tenant_config_cache()
    return cfg.routing


def _speed_reasons(offers):
    return [r for o in offers for r in o.reasons if "Mbps" in r]


def test_household_only_profile_is_sized_by_the_estimate(routing) -> None:
    profile = AdvisoryProfile(
        usage=[UsageType.STREAMING, UsageType.HOME_OFFICE], household_size=4, budget_gbp=50
    )
    offers = recommend_packages(profile, PACKAGES, routing)
    assert offers
    # 4 people x 3 devices x 25 Mbps = 300 Mbps; a 50 Mbps student line is not adequate.
    assert "FIBER_50_STUDENT" not in [o.package_code for o in offers]


def test_estimated_count_is_never_presented_as_a_stated_device_count(routing) -> None:
    profile = AdvisoryProfile(usage=[UsageType.FAMILY], household_size=4, budget_gbp=60)
    reasons = _speed_reasons(recommend_packages(profile, PACKAGES, routing))
    assert reasons
    assert all("household of 4" in r for r in reasons)
    assert not any("devices" in r for r in reasons)


def test_stated_device_count_wins_over_household_estimate(routing) -> None:
    profile = AdvisoryProfile(usage=[UsageType.FAMILY], household_size=4, device_count=8, budget_gbp=60)
    reasons = _speed_reasons(recommend_packages(profile, PACKAGES, routing))
    assert any("8 devices" in r for r in reasons)


def test_neither_known_falls_back_to_one_device_and_is_singular(routing) -> None:
    profile = AdvisoryProfile(usage=[UsageType.BASIC])
    reasons = _speed_reasons(recommend_packages(profile, PACKAGES, routing))
    assert any("1 device in your home" in r for r in reasons)
    assert not any("1 devices" in r for r in reasons)


def test_devices_per_person_is_tenant_configurable(routing) -> None:
    assert routing.recommendation.devices_per_person == 3
    light = routing.model_copy(
        update={"recommendation": routing.recommendation.model_copy(update={"devices_per_person": 1})}
    )
    profile = AdvisoryProfile(usage=[UsageType.BASIC], household_size=2)
    # 2 x 3 x 25 = 150 Mbps needed vs 2 x 1 x 25 -> floor of 50: the lighter tenant keeps cheaper lines.
    heavy_codes = {o.package_code for o in recommend_packages(profile, PACKAGES, routing)}
    light_codes = {o.package_code for o in recommend_packages(profile, PACKAGES, light)}
    assert "FIBER_50_STUDENT" not in heavy_codes
    assert "FIBER_50_STUDENT" in light_codes
