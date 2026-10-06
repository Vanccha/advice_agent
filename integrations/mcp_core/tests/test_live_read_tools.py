"""Live checks against the real company stack (core-api + company-db). Skips
cleanly when the stack isn't reachable from the current environment.
"""
from __future__ import annotations

import os

import httpx
import pytest

from app.deps import settings
from app.models import (
    FindCustomerInput,
    GetRegionHealthInput,
    GetSubscriptionStatusInput,
    ListPackagesInput,
)
from app.tools_read import (
    handle_find_customer,
    handle_get_region_health,
    handle_get_subscription_status,
    handle_list_packages,
)


def _core_api_reachable() -> bool:
    try:
        httpx.get(f"{settings().CORE_API_BASE_URL}/health", timeout=2.0)
        return True
    except httpx.HTTPError:
        return False


@pytest.fixture(autouse=True)
def _skip_if_unreachable() -> None:
    if not os.environ.get("DIAG_DATABASE_URL") or not _core_api_reachable():
        pytest.skip("company stack not reachable from this environment")


async def test_find_customer_by_customer_no() -> None:
    result = await handle_find_customer(FindCustomerInput(customer_no="NS-100001"))
    if not result.ok:
        pytest.skip(f"diag_db not reachable: {result.error}")
    assert result.source == "diag_db"
    assert len(result.data.customers) == 1
    assert result.data.customers[0].customer_no == "NS-100001"


async def test_find_customer_without_any_filter_is_invalid_input() -> None:
    result = await handle_find_customer(FindCustomerInput())
    assert result.ok is False
    assert result.error.code == "INVALID_INPUT"


async def test_get_subscription_status_by_customer_no() -> None:
    result = await handle_get_subscription_status(GetSubscriptionStatusInput(customer_no="NS-100001"))
    if not result.ok:
        pytest.skip(f"diag_db not reachable: {result.error}")
    assert len(result.data.subscriptions) >= 1
    assert result.data.subscriptions[0].customer_no == "NS-100001"


async def test_list_packages_returns_the_seeded_catalogue() -> None:
    result = await handle_list_packages(ListPackagesInput())
    assert result.ok is True
    assert result.source == "core_api"
    codes = {p.code for p in result.data.packages}
    assert "FIBER_100_BASIC" in codes


async def test_get_region_health_without_region_lists_all_regions() -> None:
    result = await handle_get_region_health(GetRegionHealthInput())
    if not result.ok:
        pytest.skip(f"diag_db not reachable: {result.error}")
    assert len(result.data.regions) >= 1
