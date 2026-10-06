"""`get_service_health` and `query_metric` must report a down/unreachable
Prometheus without raising (docs/contracts.md §3). Mocked so this never
depends on, or disrupts, the shared live Prometheus.
"""
from __future__ import annotations

import httpx
import respx

from app.deps import settings
from app.models import GetServiceHealthInput, QueryMetricInput
from app.tools import handle_get_service_health, handle_query_metric


async def test_get_service_health_reports_unreachable_without_raising() -> None:
    base_url = settings().PROMETHEUS_BASE_URL
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.get("/api/v1/query").mock(side_effect=httpx.ConnectError("refused"))
        result = await handle_get_service_health(GetServiceHealthInput())

    assert result.ok is True  # an unreachable Prometheus is a successful diagnosis
    assert result.data.prometheus_reachable is False
    assert all(s.status == "unknown" for s in result.data.services)
    assert {s.service for s in result.data.services} == {
        "core-api",
        "provisioning-worker",
        "payment-gateway",
        "ticketing",
        "notification-hub",
    }


def _mock_alerts(mock: respx.Router, alerts: list[dict]) -> None:
    """`get_service_health` merges the company's firing alerts into the view, so the
    Alertmanager call has to be mocked alongside the Prometheus one."""
    mock.get(f"{settings().ALERTMANAGER_BASE_URL}/api/v2/alerts").mock(
        return_value=httpx.Response(200, json=alerts)
    )


async def test_get_service_health_maps_up_and_down_per_job() -> None:
    base_url = settings().PROMETHEUS_BASE_URL
    body = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": {"job": "core-api"}, "value": [1700000000, "1"]},
                {"metric": {"job": "ticketing"}, "value": [1700000000, "0"]},
            ],
        },
    }
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.get("/api/v1/query").mock(return_value=httpx.Response(200, json=body))
        _mock_alerts(mock, [])
        result = await handle_get_service_health(GetServiceHealthInput())

    assert result.ok is True
    assert result.data.prometheus_reachable is True
    by_service = {s.service: s.status for s in result.data.services}
    assert by_service["core-api"] == "up"
    assert by_service["ticketing"] == "down"
    assert by_service["payment-gateway"] == "unknown"  # not in the mocked result
    assert result.data.firing_alerts == []


async def test_a_firing_alert_overrides_a_service_that_prometheus_still_calls_up() -> None:
    """The payment gateway keeps serving /metrics during an outage, so `up` stays 1 while
    the company's own alert fires. Reporting it as "up" would be a lie by omission."""
    base_url = settings().PROMETHEUS_BASE_URL
    body = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": {"job": "payment-gateway"}, "value": [1700000000, "1"]},
                {"metric": {"job": "core-api"}, "value": [1700000000, "1"]},
            ],
        },
    }
    alerts = [
        {
            "labels": {"alertname": "PaymentGatewayDown", "severity": "critical"},
            "status": {"state": "active"},
            "annotations": {"summary": "Payment gateway unreachable"},
        }
    ]
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.get("/api/v1/query").mock(return_value=httpx.Response(200, json=body))
        _mock_alerts(mock, alerts)
        result = await handle_get_service_health(GetServiceHealthInput())

    by_service = {s.service: s for s in result.data.services}
    assert by_service["payment-gateway"].status == "down"
    assert "PaymentGatewayDown" in (by_service["payment-gateway"].reason or "")
    assert by_service["core-api"].status == "up"
    assert result.data.firing_alerts == ["PaymentGatewayDown"]


async def test_unreachable_alertmanager_does_not_break_the_health_view() -> None:
    base_url = settings().PROMETHEUS_BASE_URL
    body = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": {"job": "core-api"}, "value": [1700000000, "1"]}],
        },
    }
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.get("/api/v1/query").mock(return_value=httpx.Response(200, json=body))
        mock.get(f"{settings().ALERTMANAGER_BASE_URL}/api/v2/alerts").mock(
            side_effect=httpx.ConnectError("refused")
        )
        result = await handle_get_service_health(GetServiceHealthInput())

    assert result.ok is True
    assert {s.service: s.status for s in result.data.services}["core-api"] == "up"
    assert "alertmanager unreachable" in (result.data.detail or "")


async def test_query_metric_rejects_blank_query() -> None:
    result = await handle_query_metric(QueryMetricInput(query="   "))
    assert result.ok is False
    assert result.error.code == "INVALID_INPUT"


async def test_query_metric_maps_prometheus_query_errors() -> None:
    base_url = settings().PROMETHEUS_BASE_URL
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.get("/api/v1/query").mock(
            return_value=httpx.Response(
                200, json={"status": "error", "errorType": "bad_data", "error": "invalid expression"}
            )
        )
        result = await handle_query_metric(QueryMetricInput(query="not a promql ("))

    assert result.ok is False
    assert result.error.code == "PROMETHEUS_QUERY_ERROR"


async def test_query_metric_never_raises_when_prometheus_unreachable() -> None:
    base_url = settings().PROMETHEUS_BASE_URL
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.get("/api/v1/query").mock(side_effect=httpx.ConnectError("refused"))
        result = await handle_query_metric(QueryMetricInput(query="up"))

    assert result.ok is False
    assert result.error.code == "UPSTREAM_UNAVAILABLE"
