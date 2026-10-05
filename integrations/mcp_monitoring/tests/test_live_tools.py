"""Live, read-only checks against the real Prometheus/Alertmanager. Skips
cleanly when the stack isn't reachable from the current environment. Never
flips any shared state (no chaos, no alert injection).
"""
from __future__ import annotations

import httpx
import pytest

from app.deps import settings
from app.models import GetServiceHealthInput, ListActiveAlertsInput, QueryMetricInput
from app.tools import handle_get_service_health, handle_list_active_alerts, handle_query_metric


def _prometheus_reachable() -> bool:
    try:
        httpx.get(f"{settings().PROMETHEUS_BASE_URL}/-/healthy", timeout=2.0)
        return True
    except httpx.HTTPError:
        return False


def _alertmanager_reachable() -> bool:
    try:
        httpx.get(f"{settings().ALERTMANAGER_BASE_URL}/-/healthy", timeout=2.0)
        return True
    except httpx.HTTPError:
        return False


async def test_get_service_health_reflects_the_live_stack() -> None:
    if not _prometheus_reachable():
        pytest.skip("prometheus not reachable from this environment")

    result = await handle_get_service_health(GetServiceHealthInput())
    assert result.ok is True
    assert result.source == "monitoring_api"
    assert result.data.prometheus_reachable is True
    # the live compose stack scrapes exactly these five jobs and they're up
    # per CLAUDE.md ("All are up right now")
    by_service = {s.service: s.status for s in result.data.services}
    assert by_service.get("core-api") == "up"
    assert by_service.get("ticketing") == "up"


async def test_query_metric_live_returns_the_up_vector() -> None:
    if not _prometheus_reachable():
        pytest.skip("prometheus not reachable from this environment")

    result = await handle_query_metric(QueryMetricInput(query="up"))
    assert result.ok is True
    assert result.data.result_type == "vector"
    assert len(result.data.result) >= 1


async def test_list_active_alerts_live_does_not_raise() -> None:
    if not _alertmanager_reachable():
        pytest.skip("alertmanager not reachable from this environment")

    result = await handle_list_active_alerts(ListActiveAlertsInput())
    assert result.ok is True
    assert isinstance(result.data.alerts, list)
