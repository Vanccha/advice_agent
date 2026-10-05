from __future__ import annotations

import uuid

from helpers import create_customer_with_subscription


def test_credit_cap_exceeded_returns_400(client, crm_headers):
    _, subscription = create_customer_with_subscription(client, crm_headers)
    resp = client.post(
        "/v1/credits",
        json={
            "subscription_id": subscription["id"],
            "amount_try": 999,
            "reason": "test over cap",
            "idempotency_key": f"credit-{uuid.uuid4()}",
        },
        headers=crm_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "CREDIT_LIMIT_EXCEEDED"


def test_credit_within_cap_succeeds_and_is_idempotent(client, crm_headers):
    _, subscription = create_customer_with_subscription(client, crm_headers)
    idem_key = f"credit-{uuid.uuid4()}"
    body = {
        "subscription_id": subscription["id"],
        "amount_try": 50,
        "reason": "goodwill",
        "idempotency_key": idem_key,
    }
    first = client.post("/v1/credits", json=body, headers=crm_headers)
    assert first.status_code == 201
    second = client.post("/v1/credits", json=body, headers=crm_headers)
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_resend_notification_cooldown(client, crm_headers, partner_headers):
    customer, _ = create_customer_with_subscription(client, crm_headers)
    body = {
        "customer_no": customer["customer_no"],
        "template_code": "ACTIVATION_READY",
        "channel": "sms",
    }
    first = client.post("/v1/notifications/resend", json=body, headers=partner_headers)
    assert first.status_code == 201

    second = client.post("/v1/notifications/resend", json=body, headers=partner_headers)
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "RESEND_COOLDOWN"
