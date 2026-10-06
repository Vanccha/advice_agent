from __future__ import annotations

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
    fake_gateway.set_response(
        "detect_duplicate_charges", {"duplicates": [{"payment_ids": [1, 2]}]}
    )
    fake_gateway.set_response(
        "create_structured_ticket",
        {"ticket_key": "TKT-2026-00088", "department": "BILLING", "status": "NEW", "priority": "HIGH", "created": True},
    )
    turn = client.post(
        "/api/chat",
        json={"message": "There has been a wrong charge, I was charged twice", "customer_no": "NS-100001"},
    ).json()
    assert turn["ticket_key"] == "TKT-2026-00088"

    body = json.dumps(
        {"ticket_key": "TKT-2026-00088", "old_status": "NEW", "new_status": "IN_PROGRESS"}
    ).encode()
    response = client.post("/webhooks/ticket", content=body, headers={"X-Webhook-Signature": _sign(body)})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_alert_webhook_accepted(client) -> None:
    response = client.post(
        "/webhooks/alert",
        json={"alertname": "PaymentGatewayDown", "status": "firing", "severity": "critical", "fingerprint": "fp-1"},
    )
    assert response.status_code == 200
