from __future__ import annotations


def test_post_and_list_message(client, api_headers):
    resp = client.post(
        "/api/v1/channels/billing/messages",
        headers=api_headers,
        json={"title": "Test", "text": "Test message", "severity": "info", "source": "manual", "fields": {}},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["channel_slug"] == "billing"
    assert body["title"] == "Test"
    assert body["severity"] == "info"
    assert body["created_at"].endswith("Z")

    listing = client.get("/api/v1/channels/billing/messages?limit=10", headers=api_headers)
    assert listing.status_code == 200
    data = listing.json()
    assert data["total"] >= 1
    assert any(m["title"] == "Test" for m in data["items"])


def test_unknown_channel_returns_404(client, api_headers):
    resp = client.post(
        "/api/v1/channels/does-not-exist/messages",
        headers=api_headers,
        json={"title": "x", "text": "y"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CHANNEL_NOT_FOUND"

    listing = client.get("/api/v1/channels/does-not-exist/messages", headers=api_headers)
    assert listing.status_code == 404
    assert listing.json()["error"]["code"] == "CHANNEL_NOT_FOUND"


def test_missing_api_key_returns_401(client):
    resp = client.post("/api/v1/channels/billing/messages", json={"title": "x", "text": "y"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "MISSING_API_KEY"


def test_wrong_api_key_returns_401(client):
    resp = client.get("/api/v1/channels", headers={"X-API-Key": "totally-wrong-key"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_API_KEY"


def test_list_channels_has_the_five_seeded_channels(client, api_headers):
    resp = client.get("/api/v1/channels", headers=api_headers)
    assert resp.status_code == 200
    slugs = {c["slug"] for c in resp.json()["items"]}
    assert slugs == {
        "technical-infra",
        "billing",
        "subscription-ops",
        "field-install",
        "ops-general",
    }
