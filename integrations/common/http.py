"""`CompanyApiClient` — the only way this integration layer ever writes
anything. Every mutating tool goes through here with the `partner-integration`
service-account key (`X-API-Key`); SQL writes are impossible by construction
(see `readonly_db.py`).

Responsibilities:
- attach `X-API-Key: <COMPANY_API_KEY>` to every request;
- retry only on connection-level failures (refused connection, DNS, connect
  timeout) — never on an application-level 4xx/5xx, since those are
  meaningful answers (e.g. `409 ILLEGAL_TRANSITION`), not transient faults;
- map the company's error envelope `{"error": {"code","message","details"}}`
  into `ToolResult.fail(...)`, preserving the company's own error code.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

from .result import Source, ToolResult, fail, ok

logger = logging.getLogger(__name__)

# Only transport-level failures are retried. An HTTP response that came back
# (even a 503) is a real answer from the company and is mapped, not retried.
_RETRYABLE_EXCEPTIONS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout)


class CompanyApiClient:
    """Thin async httpx wrapper around one company REST base URL."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 10.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.2,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> None:
        headers = {"X-API-Key": api_key}
        if extra_headers:
            headers.update(extra_headers)
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout, headers=headers)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "CompanyApiClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    async def _request_with_retry(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        attempt = 0
        while True:
            try:
                return await self._client.request(method, path, **kwargs)
            except _RETRYABLE_EXCEPTIONS:
                attempt += 1
                if attempt > self._max_retries:
                    raise
                logger.warning(
                    "connection error calling %s %s%s (attempt %d/%d), retrying",
                    method,
                    self._base_url,
                    path,
                    attempt,
                    self._max_retries,
                )
                await asyncio.sleep(self._retry_backoff_seconds * attempt)

    async def request_raw(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Perform a request and return the raw `httpx.Response`, applying the
        same connection-retry policy as `call()` but *not* mapping the result
        into a `ToolResult`. For the rare tool (e.g. `get_gateway_health`)
        that needs to interpret a non-2xx response itself instead of treating
        it as a tool failure. Transport-level errors (even after retries)
        propagate as exceptions here — callers that must never raise should
        catch them.
        """
        return await self._request_with_retry(method, path, **kwargs)

    async def call(
        self,
        method: str,
        path: str,
        source: Source,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> ToolResult[Any]:
        """Perform one request and always return a `ToolResult`, never raise.

        On a 2xx response, `data` is the parsed JSON body (dict/list/None).
        On a non-2xx response, the company's error envelope is unpacked into
        `error.code` / `error.message` / `error.details` (code preserved
        verbatim). On a transport failure (even after retries), returns
        `UPSTREAM_UNAVAILABLE`.
        """
        try:
            response = await self._request_with_retry(method, path, params=params, json=json)
        except _RETRYABLE_EXCEPTIONS as exc:
            return fail(
                "UPSTREAM_UNAVAILABLE",
                f"{self._base_url}{path} unreachable: {exc!s}",
                source,
                method=method,
                path=path,
            )
        except httpx.HTTPError as exc:
            return fail(
                "UPSTREAM_UNAVAILABLE",
                f"request to {self._base_url}{path} failed: {exc!s}",
                source,
                method=method,
                path=path,
            )

        if 200 <= response.status_code < 300:
            body = _safe_json(response)
            return ok(body, source)

        code, message, details = _parse_error(response)
        details.setdefault("http_status", response.status_code)
        return fail(code, message, source, **details)

    async def get(self, path: str, source: Source, *, params: Optional[dict[str, Any]] = None) -> ToolResult[Any]:
        return await self.call("GET", path, source, params=params)

    async def post(self, path: str, source: Source, *, json: Optional[dict[str, Any]] = None) -> ToolResult[Any]:
        return await self.call("POST", path, source, json=json)

    async def patch(self, path: str, source: Source, *, json: Optional[dict[str, Any]] = None) -> ToolResult[Any]:
        return await self.call("PATCH", path, source, json=json)


def _safe_json(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _parse_error(response: httpx.Response) -> tuple[str, str, dict[str, Any]]:
    """Unpack the company error envelope `{"error": {"code","message","details"}}`.

    Falls back to a synthetic `HTTP_<status>` code for anything that doesn't
    match the shape (e.g. a raw FastAPI 422 `{"detail": [...]}`, or an
    upstream proxy error with no JSON body at all).
    """
    try:
        body = response.json()
    except ValueError:
        text = (response.text or response.reason_phrase or "").strip()
        return f"HTTP_{response.status_code}", text[:500] or f"HTTP {response.status_code}", {}

    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return (
                str(err.get("code", f"HTTP_{response.status_code}")),
                str(err.get("message", "company API error")),
                dict(err.get("details") or {}),
            )
        if "detail" in body:
            # FastAPI's own 422 validation-error shape, not the company envelope.
            return "VALIDATION_ERROR", str(body["detail"])[:500], {"detail": body["detail"]}

    return f"HTTP_{response.status_code}", str(body)[:500], {}
