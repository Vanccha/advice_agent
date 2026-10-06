from __future__ import annotations


def _create_succeeding_charge(client, auth_headers, customer_ref, idem_key, amount=200.0):
    client.post(
        "/psp/v1/control",
        json={"failure_rate": 0.0, "force_failure_code": None},
        headers=auth_headers,
    )
    resp = client.post(
        "/psp/v1/charges",
        json={
            "amount_gbp": amount,
            "currency": "GBP",
            "customer_ref": customer_ref,
            "method": "card",
            "card_token": "tok_test_5",
            "idempotency_key": idem_key,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "succeeded"
    return resp.json()["charge_ref"]


def test_full_refund_marks_refunded(client, auth_headers):
    charge_ref = _create_succeeding_charge(client, auth_headers, "NS-10", "refund-full-1")

    resp = client.post(
        f"/psp/v1/charges/{charge_ref}/refunds",
        json={"amount_gbp": 200.0, "reason": "customer request"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["charge_status"] == "refunded"

    get_resp = client.get(f"/psp/v1/charges/{charge_ref}", headers=auth_headers)
    assert get_resp.json()["status"] == "refunded"


def test_partial_then_full_refund(client, auth_headers):
    charge_ref = _create_succeeding_charge(client, auth_headers, "NS-11", "refund-partial-1", amount=200.0)

    first = client.post(
        f"/psp/v1/charges/{charge_ref}/refunds",
        json={"amount_gbp": 50.0},
        headers=auth_headers,
    )
    assert first.status_code == 201
    assert first.json()["charge_status"] == "partially_refunded"

    get_after_partial = client.get(f"/psp/v1/charges/{charge_ref}", headers=auth_headers)
    assert get_after_partial.json()["status"] == "partially_refunded"

    second = client.post(
        f"/psp/v1/charges/{charge_ref}/refunds",
        json={"amount_gbp": 150.0},
        headers=auth_headers,
    )
    assert second.status_code == 201
    assert second.json()["charge_status"] == "refunded"


def test_second_full_refund_conflicts(client, auth_headers):
    charge_ref = _create_succeeding_charge(client, auth_headers, "NS-12", "refund-conflict-1")

    client.post(
        f"/psp/v1/charges/{charge_ref}/refunds",
        json={"amount_gbp": 200.0},
        headers=auth_headers,
    )
    again = client.post(
        f"/psp/v1/charges/{charge_ref}/refunds",
        json={"amount_gbp": 10.0},
        headers=auth_headers,
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "ALREADY_REFUNDED"


def test_refund_exceeds_charge(client, auth_headers):
    charge_ref = _create_succeeding_charge(client, auth_headers, "NS-13", "refund-exceed-1", amount=100.0)

    resp = client.post(
        f"/psp/v1/charges/{charge_ref}/refunds",
        json={"amount_gbp": 150.0},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "REFUND_EXCEEDS_CHARGE"
