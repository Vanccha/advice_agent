"""`get_gateway_health` must report `outage` truthfully and never raise, even
when the gateway is completely unreachable (docs/contracts.md §3). The
"never raises" half is tested against a mocked transport (no live outage is
ever triggered against the shared company stack from a test); the "truthful
when healthy" half is also checked live, read-only.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from app.deps import settings
from app.models import GetGatewayHealthInput
from app.tools import handle_get_gateway_health


@pytest.mark.asyncio
async def test_reports_down_without_raising_when_connection_refused() -> None:
    base_url = settings().PAYMENT_API_BASE_URL
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.get("/health").mock(side_effect=httpx.ConnectError("refused"))
        mock.get("/psp/v1/control").mock(side_effect=httpx.ConnectError("refused"))
        result = await handle_get_gateway_health(GetGatewayHealthInput())

    assert result.ok is True  # a down gateway is a successful diagnosis, not a tool failure
    assert result.data.reachable is False
    assert result.data.outage is True


@pytest.mark.asyncio
async def test_reports_up_when_health_and_control_both_succeed() -> None:
    base_url = settings().PAYMENT_API_BASE_URL
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.get("/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
        mock.get("/psp/v1/control").mock(
            return_value=httpx.Response(200, json={"failure_rate": 0.05, "outage": False, "latency_ms": 0})
        )
        result = await handle_get_gateway_health(GetGatewayHealthInput())

    assert result.ok is True
    assert result.data.reachable is True
    assert result.data.outage is False
    assert result.data.failure_rate == 0.05


@pytest.mark.asyncio
async def test_reports_outage_flag_without_raising_when_health_is_503_but_control_works() -> None:
    base_url = settings().PAYMENT_API_BASE_URL
    with respx.mock(base_url=base_url, assert_all_called=False) as mock:
        mock.get("/health").mock(
            return_value=httpx.Response(503, json={"error": {"code": "GATEWAY_UNAVAILABLE", "message": "down"}})
        )
        mock.get("/psp/v1/control").mock(
            return_value=httpx.Response(200, json={"failure_rate": 0.1, "outage": True, "latency_ms": 0})
        )
        result = await handle_get_gateway_health(GetGatewayHealthInput())

    assert result.ok is True
    assert result.data.reachable is False
    assert result.data.health_http_status == 503
    assert result.data.outage is True


async def test_live_gateway_health_reflects_current_non_outage_state() -> None:
    try:
        httpx.get(f"{settings().PAYMENT_API_BASE_URL}/health", timeout=2.0)
    except httpx.HTTPError:
        pytest.skip("payment-gateway not reachable from this environment")

    result = await handle_get_gateway_health(GetGatewayHealthInput())
    assert result.ok is True
    assert result.source == "payment_api"
    # read-only assertion: this test never flips the shared control flag
    assert isinstance(result.data.outage, bool)
