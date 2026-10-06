from __future__ import annotations


def test_outage_blocks_business_endpoints_and_health(client, auth_headers):
    toggled = client.post("/psp/v1/control", json={"outage": True}, headers=auth_headers)
    assert toggled.status_code == 200
    assert toggled.json()["outage"] is True

    health = client.get("/health")
    assert health.status_code == 503
    body = health.json()
    assert body["status"] == "unavailable"
    assert body["service"]
    assert body["version"]

    charges_resp = client.get("/psp/v1/charges", headers=auth_headers)
    assert charges_resp.status_code == 503
    assert charges_resp.json()["error"]["code"] == "GATEWAY_UNAVAILABLE"

    create_resp = client.post(
        "/psp/v1/charges",
        json={
            "amount_gbp": 10.0,
            "currency": "GBP",
            "customer_ref": "NS-20",
            "method": "card",
            "card_token": "tok_test_1",
            "idempotency_key": "during-outage",
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 503
    assert create_resp.json()["error"]["code"] == "GATEWAY_UNAVAILABLE"

    # control stays reachable so chaos tooling can turn the outage back off
    control_get = client.get("/psp/v1/control", headers=auth_headers)
    assert control_get.status_code == 200

    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    assert "psp_outage 1.0" in metrics_resp.text

    restored = client.post("/psp/v1/control", json={"outage": False}, headers=auth_headers)
    assert restored.status_code == 200
    assert restored.json()["outage"] is False

    health_after = client.get("/health")
    assert health_after.status_code == 200
    assert health_after.json()["status"] == "ok"

    charges_after = client.get("/psp/v1/charges", headers=auth_headers)
    assert charges_after.status_code == 200

    metrics_after = client.get("/metrics")
    assert "psp_outage 0.0" in metrics_after.text
