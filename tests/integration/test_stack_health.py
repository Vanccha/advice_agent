"""The stack answers as a whole — the cheapest possible end-to-end canary."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_core_api_health_and_openapi(core_api) -> None:
    health = core_api.get("/health").json()
    assert health["status"] == "ok"
    schema = core_api.get("/openapi.json").json()
    assert "openapi" in schema
    assert any(path.startswith("/v1/") for path in schema["paths"])


def test_company_services_are_up(ticketing_api, notification_api, payment_api) -> None:
    assert ticketing_api.get("/health").json()["status"] == "ok"
    assert notification_api.get("/health").json()["status"] == "ok"
    assert payment_api.get("/psp/v1/control").status_code == 200


def test_seed_catalogue_is_present(core_api) -> None:
    body = core_api.get("/v1/packages").json()
    items = body["items"] if isinstance(body, dict) else body
    assert len(items) >= 7, "the company should ship its full package catalogue"
    codes = {item["code"] for item in items}
    assert {"FIBER_50_OGRENCI", "FIBER_1000_PREMIUM"} <= codes


def test_customer_base_is_seeded(core_api) -> None:
    body = core_api.get("/v1/customers", params={"limit": 1}).json()
    assert body["total"] >= 200, "the company should have ~200 customers on file"
