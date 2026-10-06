"""`POST /webhooks/alertmanager` must normalize a realistic Alertmanager
payload correctly and always answer 2xx, even when the forward target
(`ASSISTANT_ALERT_WEBHOOK_URL`) is unreachable — the assistant container may
not exist yet (docs/contracts.md §3). Mocked: never depends on a real
assistant container.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.deps import alert_webhook_url
from app.main import app as monitoring_app
from app.models import AlertmanagerWebhookAlert
from app.webhooks import normalize_alert

# The installed mcp SDK's StreamableHTTPSessionManager.run() may only be
# entered once per process (it raises RuntimeError on a second call), and
# `app.main` builds exactly one MCPServer/session manager at import time —
# same as it would under a single uvicorn process in production. A fresh
# `with TestClient(...)` per test would each trigger the FastAPI lifespan
# (startup+shutdown), so this module instead shares one TestClient (one
# lifespan cycle) across every test here.
@pytest.fixture(scope="module")
def client():
    with TestClient(monitoring_app) as c:
        yield c


def _realistic_alertmanager_payload() -> dict:
    return {
        "receiver": "integration-webhook",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "PaymentGatewayDown",
                    "severity": "critical",
                    "department": "TECHNICAL_INFRA",
                    "job": "payment-gateway",
                },
                "annotations": {
                    "summary": "Payment gateway unreachable",
                    "description": "payment-gateway servisi bir dakikadir health check'e yanit vermiyor.",
                },
                "startsAt": "2026-10-05T12:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "fingerprint": "ab12cd34ef567890",
            }
        ],
        "groupLabels": {"alertname": "PaymentGatewayDown"},
        "commonLabels": {"alertname": "PaymentGatewayDown", "severity": "critical"},
        "commonAnnotations": {},
        "externalURL": "http://alertmanager:9093",
        "version": "4",
        "groupKey": '{}:{alertname="PaymentGatewayDown"}',
    }


def _split(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}", parts.path


def test_normalize_alert_maps_every_contract_field() -> None:
    raw = _realistic_alertmanager_payload()["alerts"][0]
    alert = AlertmanagerWebhookAlert(**raw)
    normalized = normalize_alert(alert)

    assert normalized.alertname == "PaymentGatewayDown"
    assert normalized.status == "firing"
    assert normalized.severity == "critical"
    assert normalized.department == "TECHNICAL_INFRA"
    assert normalized.fingerprint == "ab12cd34ef567890"
    assert normalized.summary == "Payment gateway unreachable"
    assert "health check" in normalized.description
    assert normalized.labels["alertname"] == "PaymentGatewayDown"
    assert normalized.starts_at == "2026-10-05T12:00:00Z"
    assert normalized.source == "netswift-alertmanager"


def test_webhook_forwards_and_returns_2xx_when_target_is_up(client: TestClient) -> None:
    base, path = _split(alert_webhook_url())
    with respx.mock(base_url=base, assert_all_called=False) as mock:
        mock.post(path).mock(return_value=httpx.Response(200, json={"ok": True}))
        response = client.post("/webhooks/alertmanager", json=_realistic_alertmanager_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 1
    assert body["results"][0]["forwarded"] is True
    assert body["results"][0]["fingerprint"] == "ab12cd34ef567890"


def test_webhook_still_returns_2xx_when_forward_target_is_dead(client: TestClient) -> None:
    base, path = _split(alert_webhook_url())
    with respx.mock(base_url=base, assert_all_called=False) as mock:
        mock.post(path).mock(side_effect=httpx.ConnectError("connection refused"))
        response = client.post("/webhooks/alertmanager", json=_realistic_alertmanager_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 1
    assert body["results"][0]["forwarded"] is False


def test_webhook_handles_an_empty_alert_list(client: TestClient) -> None:
    response = client.post(
        "/webhooks/alertmanager", json={"receiver": "integration-webhook", "status": "firing", "alerts": []}
    )
    assert response.status_code == 200
    assert response.json() == {"received": 0, "results": []}


def test_health_and_metrics_still_reachable_alongside_the_webhook_route(client: TestClient) -> None:
    health = client.get("/health")
    metrics = client.get("/metrics")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert metrics.status_code == 200
    assert b"mcp_monitoring_alert_forwards_total" in metrics.content
