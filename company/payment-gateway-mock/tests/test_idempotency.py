from __future__ import annotations


def test_same_idempotency_key_returns_same_charge(client, auth_headers):
    client.post(
        "/psp/v1/control",
        json={"failure_rate": 0.0, "force_failure_code": None},
        headers=auth_headers,
    )

    body = {
        "amount_gbp": 459.0,
        "currency": "GBP",
        "customer_ref": "NS-100042",
        "method": "card",
        "card_token": "tok_test_1",
        "idempotency_key": "idem-key-1",
    }

    first = client.post("/psp/v1/charges", json=body, headers=auth_headers)
    assert first.status_code == 201
    first_ref = first.json()["charge_ref"]
    assert first.json()["status"] == "succeeded"

    second = client.post("/psp/v1/charges", json=body, headers=auth_headers)
    assert second.status_code == 200
    assert second.json()["charge_ref"] == first_ref

    listing = client.get(
        "/psp/v1/charges", params={"customer_ref": "NS-100042"}, headers=auth_headers
    )
    matches = [c for c in listing.json()["items"] if c["idempotency_key"] == "idem-key-1"]
    assert len(matches) == 1


def test_different_idempotency_keys_create_different_charges(client, auth_headers):
    client.post(
        "/psp/v1/control",
        json={"failure_rate": 0.0, "force_failure_code": None},
        headers=auth_headers,
    )
    body = {
        "amount_gbp": 100.0,
        "currency": "GBP",
        "customer_ref": "NS-100043",
        "method": "card",
        "card_token": "tok_test_2",
        "idempotency_key": "idem-a",
    }
    first = client.post("/psp/v1/charges", json=body, headers=auth_headers)
    body2 = {**body, "idempotency_key": "idem-b"}
    second = client.post("/psp/v1/charges", json=body2, headers=auth_headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["charge_ref"] != second.json()["charge_ref"]
