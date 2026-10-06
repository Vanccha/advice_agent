from __future__ import annotations

VALID_FAILURE_CODES = {
    "INSUFFICIENT_FUNDS",
    "CARD_DECLINED",
    "DO_NOT_HONOR",
    "TIMEOUT",
    "GATEWAY_ERROR",
}


def _charge_body(customer_ref: str, idem_key: str, amount: float = 100.0) -> dict:
    return {
        "amount_gbp": amount,
        "currency": "GBP",
        "customer_ref": customer_ref,
        "method": "card",
        "card_token": "tok_test_9",
        "idempotency_key": idem_key,
    }


def test_force_failure_code_wins(client, auth_headers):
    client.post(
        "/psp/v1/control",
        json={"force_failure_code": "CARD_DECLINED", "failure_rate": 0.0},
        headers=auth_headers,
    )
    resp = client.post(
        "/psp/v1/charges",
        json=_charge_body("NS-1", "force-1"),
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "failed"
    assert data["failure_code"] == "CARD_DECLINED"
    assert data["failure_message"]


def test_failure_rate_zero_always_succeeds(client, auth_headers):
    client.post(
        "/psp/v1/control",
        json={"failure_rate": 0.0, "force_failure_code": None},
        headers=auth_headers,
    )
    for i in range(15):
        resp = client.post(
            "/psp/v1/charges",
            json=_charge_body("NS-2", f"rate0-{i}"),
            headers=auth_headers,
        )
        assert resp.json()["status"] == "succeeded"


def test_failure_rate_one_always_fails(client, auth_headers):
    client.post(
        "/psp/v1/control",
        json={"failure_rate": 1.0, "force_failure_code": None},
        headers=auth_headers,
    )
    for i in range(15):
        resp = client.post(
            "/psp/v1/charges",
            json=_charge_body("NS-3", f"rate1-{i}"),
            headers=auth_headers,
        )
        data = resp.json()
        assert data["status"] == "failed"
        assert data["failure_code"] in VALID_FAILURE_CODES
