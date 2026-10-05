import httpx
import pytest
import respx

from common.http import CompanyApiClient


@pytest.mark.asyncio
async def test_successful_get_returns_ok_envelope() -> None:
    client = CompanyApiClient("http://core-api.test", "k")
    try:
        with respx.mock(base_url="http://core-api.test") as mock:
            mock.get("/v1/packages").mock(return_value=httpx.Response(200, json={"items": [], "total": 0}))
            result = await client.get("/v1/packages", "core_api")
        assert result.ok is True
        assert result.source == "core_api"
        assert result.data == {"items": [], "total": 0}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_company_error_envelope_preserves_code_and_details() -> None:
    client = CompanyApiClient("http://core-api.test", "k")
    try:
        with respx.mock(base_url="http://core-api.test") as mock:
            mock.post("/v1/refunds").mock(
                return_value=httpx.Response(
                    403,
                    json={
                        "error": {
                            "code": "SCOPE_DENIED",
                            "message": "Service account 'partner-integration' lacks the 'billing:refund' scope.",
                            "details": {"required_scope": "billing:refund"},
                        }
                    },
                )
            )
            result = await client.post("/v1/refunds", "core_api", json={"payment_id": 1, "amount_try": 1, "reason": "x"})
        assert result.ok is False
        assert result.error.code == "SCOPE_DENIED"
        assert result.error.details["required_scope"] == "billing:refund"
        assert result.error.details["http_status"] == 403
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_illegal_transition_code_preserved() -> None:
    client = CompanyApiClient("http://core-api.test", "k")
    try:
        with respx.mock(base_url="http://core-api.test") as mock:
            mock.post("/v1/subscriptions/1/transitions").mock(
                return_value=httpx.Response(
                    409, json={"error": {"code": "ILLEGAL_TRANSITION", "message": "bad transition", "details": {}}}
                )
            )
            result = await client.post("/v1/subscriptions/1/transitions", "core_api", json={"to_status": "active"})
        assert result.ok is False
        assert result.error.code == "ILLEGAL_TRANSITION"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_fastapi_validation_error_maps_to_validation_error_code() -> None:
    client = CompanyApiClient("http://core-api.test", "k")
    try:
        with respx.mock(base_url="http://core-api.test") as mock:
            mock.get("/v1/packages/does-not-exist").mock(
                return_value=httpx.Response(422, json={"detail": [{"loc": ["path", "code"], "msg": "bad", "type": "x"}]})
            )
            result = await client.get("/v1/packages/does-not-exist", "core_api")
        assert result.ok is False
        assert result.error.code == "VALIDATION_ERROR"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_connection_error_maps_to_upstream_unavailable_without_raising() -> None:
    client = CompanyApiClient("http://unreachable.test", "k", max_retries=0, timeout=1.0)
    try:
        with respx.mock(base_url="http://unreachable.test") as mock:
            mock.get("/health").mock(side_effect=httpx.ConnectError("refused"))
            result = await client.get("/health", "core_api")
        assert result.ok is False
        assert result.error.code == "UPSTREAM_UNAVAILABLE"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_retries_on_connection_error_then_succeeds() -> None:
    client = CompanyApiClient("http://flaky.test", "k", max_retries=2, retry_backoff_seconds=0.01)
    try:
        with respx.mock(base_url="http://flaky.test") as mock:
            route = mock.get("/health")
            route.side_effect = [
                httpx.ConnectError("refused"),
                httpx.Response(200, json={"status": "ok"}),
            ]
            result = await client.get("/health", "core_api")
        assert result.ok is True
        assert result.data == {"status": "ok"}
        assert route.call_count == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_does_not_retry_on_4xx_application_error() -> None:
    client = CompanyApiClient("http://core-api.test", "k")
    try:
        with respx.mock(base_url="http://core-api.test") as mock:
            route = mock.post("/v1/refunds").mock(
                return_value=httpx.Response(403, json={"error": {"code": "SCOPE_DENIED", "message": "no"}})
            )
            await client.post("/v1/refunds", "core_api", json={})
        assert route.call_count == 1  # no retry on a real (non-connection) error
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_api_key_header_is_sent() -> None:
    client = CompanyApiClient("http://core-api.test", "secret-key-123")
    try:
        with respx.mock(base_url="http://core-api.test") as mock:
            route = mock.get("/v1/packages").mock(return_value=httpx.Response(200, json={"items": [], "total": 0}))
            await client.get("/v1/packages", "core_api")
        assert route.calls.last.request.headers["X-API-Key"] == "secret-key-123"
    finally:
        await client.aclose()
