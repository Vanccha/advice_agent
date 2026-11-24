from __future__ import annotations

from api.tests.conftest import login_as

import hashlib
import hmac
import json

SECRET = "test-webhook-secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_ticket_webhook_rejects_bad_signature(client) -> None:
    body = json.dumps({"ticket_key": "TKT-2026-00001", "new_status": "IN_PROGRESS"}).encode()
    response = client.post(
        "/webhooks/ticket", content=body, headers={"X-Webhook-Signature": "sha256=deadbeef"}
    )
    assert response.status_code == 401


def test_ticket_webhook_rejects_unsigned(client) -> None:
    body = json.dumps({"ticket_key": "TKT-2026-00001", "new_status": "IN_PROGRESS"}).encode()
    response = client.post("/webhooks/ticket", content=body)
    assert response.status_code == 401


def test_ticket_webhook_accepts_valid_signature(client, fake_gateway) -> None:
    login_as(client, "NS-100001")
    fake_gateway.set_response(
        "detect_duplicate_charges", {"duplicates": [{"payment_ids": [1, 2]}]}
    )
    fake_gateway.set_response(
        "create_structured_ticket",
        {"ticket_key": "TKT-2026-00088", "department": "BILLING", "status": "NEW", "priority": "HIGH", "created": True},
    )
    turn = client.post(
        "/api/chat",
        json={"message": "There has been a wrong charge, I was charged twice"},
    ).json()
    assert turn["ticket_key"] == "TKT-2026-00088"

    body = json.dumps(
        {"ticket_key": "TKT-2026-00088", "old_status": "NEW", "new_status": "IN_PROGRESS"}
    ).encode()
    response = client.post("/webhooks/ticket", content=body, headers={"X-Webhook-Signature": _sign(body)})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def _alert_body() -> bytes:
    return json.dumps(
        {"alertname": "PaymentGatewayDown", "status": "firing", "severity": "critical", "fingerprint": "fp-1"}
    ).encode()


def test_alert_webhook_accepts_valid_signature(client) -> None:
    body = _alert_body()
    response = client.post(
        "/webhooks/alert", content=body,
        headers={"X-Webhook-Signature": _sign(body), "Content-Type": "application/json"},
    )
    assert response.status_code == 200


def test_alert_webhook_rejects_unsigned(client) -> None:
    assert client.post("/webhooks/alert", content=_alert_body()).status_code == 401


def test_alert_webhook_rejects_bad_signature(client) -> None:
    response = client.post(
        "/webhooks/alert", content=_alert_body(), headers={"X-Webhook-Signature": "sha256=deadbeef"}
    )
    assert response.status_code == 401


def test_alert_webhook_rejects_signature_for_other_body(client) -> None:
    response = client.post(
        "/webhooks/alert", content=_alert_body(), headers={"X-Webhook-Signature": _sign(b"{}")}
    )
    assert response.status_code == 401
