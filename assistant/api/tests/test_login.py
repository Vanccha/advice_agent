from __future__ import annotations


def test_login_rejects_bad_format(client) -> None:
    response = client.post("/api/login", json={"customer_no": "not-a-customer"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_FORMAT"


def test_login_accepts_known_customer_and_sets_cookie(client, fake_gateway) -> None:
    response = client.post("/api/login", json={"customer_no": "NS-100001"})
    assert response.status_code == 200
    assert response.cookies.get("demo_customer_no") == "NS-100001"


def test_login_rejects_unknown_customer(client, fake_gateway) -> None:
    from mcp_gateway.types import ToolCallOutcome

    fake_gateway.set_response("find_customer", ToolCallOutcome(ok=False, error_code="NOT_FOUND"))
    response = client.post("/api/login", json={"customer_no": "NS-999999"})
    assert response.status_code == 404
