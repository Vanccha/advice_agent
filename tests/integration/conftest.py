"""Fixtures for cross-service integration tests.

These tests talk to the running compose stack over HTTP, exactly like a real client would,
and skip (rather than fail) when a service is not up — so `make test` stays useful before
`make up`.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


class ServiceProbe:
    """Minimal HTTP wrapper with an API key and readable assertions."""

    def __init__(self, name: str, base_url: str, api_key: str | None = None) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if extra:
            headers.update(extra)
        return headers

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = self.headers(kwargs.pop("headers", None))
        return httpx.request(
            method, f"{self.base_url}{path}", headers=headers, timeout=20.0, **kwargs
        )

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", path, **kwargs)

    def is_up(self, path: str = "/health") -> bool:
        try:
            return self.request("GET", path).status_code < 500
        except httpx.HTTPError:
            return False


def _probe_or_skip(probe: ServiceProbe, health_path: str = "/health") -> ServiceProbe:
    if not probe.is_up(health_path):
        pytest.skip(f"{probe.name} is not reachable at {probe.base_url} (run `make up`)")
    return probe


@pytest.fixture(scope="session")
def core_api() -> ServiceProbe:
    return _probe_or_skip(
        ServiceProbe(
            "core-api",
            _env("CORE_API_BASE_URL", "http://core-api:8000"),
            _env("CORE_API_KEY_PARTNER", "netswift_partner_key_change_me"),
        )
    )


@pytest.fixture(scope="session")
def core_api_crm() -> ServiceProbe:
    """Full-privilege company account — used to set up test state, never by the product."""
    return _probe_or_skip(
        ServiceProbe(
            "core-api(crm)",
            _env("CORE_API_BASE_URL", "http://core-api:8000"),
            _env("CORE_API_KEY_CRM", "netswift_crm_key_change_me"),
        )
    )


@pytest.fixture(scope="session")
def payment_api() -> ServiceProbe:
    probe = ServiceProbe(
        "payment-gateway",
        _env("PAYMENT_API_BASE_URL", "http://payment-gateway:8000"),
        _env("PSP_API_KEY", "netswift_psp_key_change_me"),
    )
    # The gateway answers /health with 503 while an outage is injected, so probe the control
    # endpoint instead, which stays available by design.
    return _probe_or_skip(probe, "/psp/v1/control")


@pytest.fixture(scope="session")
def ticketing_api() -> ServiceProbe:
    return _probe_or_skip(
        ServiceProbe(
            "ticketing",
            _env("TICKETING_API_BASE_URL", "http://ticketing:8000"),
            _env("TICKETING_API_KEY", "netswift_tkt_key_change_me"),
        )
    )


@pytest.fixture(scope="session")
def notification_api() -> ServiceProbe:
    return _probe_or_skip(
        ServiceProbe(
            "notification-hub",
            _env("NOTIFICATION_API_BASE_URL", "http://notification-hub:8000"),
            _env("NOTIFY_API_KEY", "netswift_notify_key_change_me"),
        )
    )


@pytest.fixture(scope="session")
def assistant_api() -> ServiceProbe:
    return _probe_or_skip(
        ServiceProbe("assistant", _env("ASSISTANT_BASE_URL", "http://assistant:8000"))
    )


@pytest.fixture(scope="session")
def diag_dsn() -> str:
    dsn = os.environ.get("DIAG_DATABASE_URL")
    if not dsn:
        pytest.skip("DIAG_DATABASE_URL is not configured")
    return dsn


@pytest.fixture
def wait_until() -> Iterator[Callable[..., Any]]:
    """Poll a predicate until it returns a truthy value, or fail with a readable message."""

    def _wait(
        predicate: Callable[[], Any],
        *,
        timeout: float = 30.0,
        interval: float = 1.0,
        message: str = "condition not met",
    ) -> Any:
        deadline = time.monotonic() + timeout
        last: Any = None
        while time.monotonic() < deadline:
            last = predicate()
            if last:
                return last
            time.sleep(interval)
        raise AssertionError(f"{message} (waited {timeout:.0f}s, last value: {last!r})")

    yield _wait
