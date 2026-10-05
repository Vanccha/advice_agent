from __future__ import annotations


def test_missing_api_key_rejected(client):
    resp = client.get("/psp/v1/charges")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "MISSING_API_KEY"


def test_wrong_api_key_rejected(client):
    resp = client.get("/psp/v1/charges", headers={"X-API-Key": "definitely-wrong"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_API_KEY"


def test_control_requires_api_key_too(client):
    resp = client.get("/psp/v1/control")
    assert resp.status_code == 401


def test_health_and_metrics_need_no_key(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
