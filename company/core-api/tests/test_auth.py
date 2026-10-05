from __future__ import annotations

from helpers import create_customer_with_subscription


def test_missing_api_key_is_401(client):
    resp = client.get("/v1/customers")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "MISSING_API_KEY"


def test_wrong_api_key_is_401(client):
    resp = client.get("/v1/customers", headers={"X-API-Key": "not-a-real-key"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_API_KEY"


def test_crm_key_can_read_customers(client, crm_headers):
    resp = client.get("/v1/customers", headers=crm_headers)
    assert resp.status_code == 200


def test_partner_account_cannot_refund(client, crm_headers, partner_headers):
    _, subscription = create_customer_with_subscription(client, crm_headers)
    # Partner lacks billing:refund -> must be denied even for a well-formed request.
    resp = client.post(
        "/v1/refunds",
        json={"payment_id": 1, "amount_try": 10, "reason": "test"},
        headers=partner_headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "SCOPE_DENIED"


def test_partner_account_cannot_write_customers(client, partner_headers):
    from helpers import make_customer_payload

    resp = client.post("/v1/customers", json=make_customer_payload(), headers=partner_headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "SCOPE_DENIED"
