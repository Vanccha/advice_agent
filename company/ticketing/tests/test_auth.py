from __future__ import annotations


def test_missing_api_key_returns_401(client) -> None:
    resp = client.get("/api/v1/tickets")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "MISSING_API_KEY"


def test_wrong_api_key_returns_401(client) -> None:
    resp = client.get("/api/v1/tickets", headers={"X-API-Key": "not-the-right-key"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_API_KEY"


def test_correct_api_key_succeeds(client, auth_headers) -> None:
    resp = client.get("/api/v1/tickets", headers=auth_headers)
    assert resp.status_code == 200


def test_post_without_api_key_returns_401(client) -> None:
    resp = client.post(
        "/api/v1/tickets",
        json={
            "department": "BILLING",
            "issue_type": "other",
            "priority": "LOW",
            "subject": "x",
            "body": "x",
            "source": "api",
        },
    )
    assert resp.status_code == 401
