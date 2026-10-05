from __future__ import annotations

import logging
from typing import Any

import httpx

from shared.errors import ApiError, UpstreamUnavailable

logger = logging.getLogger(__name__)


class ServiceClient:
    """Thin HTTP client for service-to-service calls inside the company network."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 10.0,
        upstream_name: str = "upstream",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.upstream_name = upstream_name

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if extra:
            headers.update(extra)
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        raise_for_status: bool = True,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        try:
            response = httpx.request(
                method,
                url,
                json=json_body,
                params=params,
                headers=self._headers(headers),
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            logger.warning("%s unreachable: %s", self.upstream_name, exc)
            raise UpstreamUnavailable(
                "UPSTREAM_UNAVAILABLE",
                f"{self.upstream_name} is not reachable.",
                upstream=self.upstream_name,
            ) from exc
        if raise_for_status and response.status_code >= 400:
            payload: dict[str, Any] = {}
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            raise ApiError(
                code=error.get("code", "UPSTREAM_ERROR"),
                message=error.get("message", f"{self.upstream_name} returned {response.status_code}."),
                status_code=response.status_code if response.status_code < 500 else 503,
                details={"upstream": self.upstream_name, **(error.get("details") or {})},
            )
        return response

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", path, **kwargs)
