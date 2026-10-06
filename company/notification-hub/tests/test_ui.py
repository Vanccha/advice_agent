from __future__ import annotations


def test_index_lists_channels_with_display_names(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Billing" in resp.text
    assert "Technical Infrastructure" in resp.text
    assert "Subscription Operations" in resp.text
    assert "Field Installation Team" in resp.text
    assert "Operations General" in resp.text


def test_channel_stream_returns_200_and_shows_display_name(client):
    resp = client.get("/c/billing")
    assert resp.status_code == 200
    assert "Billing" in resp.text
    assert 'content="5"' in resp.text  # auto-refresh meta tag


def test_channel_stream_shows_posted_message(client, api_headers):
    client.post(
        "/api/v1/channels/billing/messages",
        headers=api_headers,
        json={"title": "UI test", "text": "This message should appear in the stream", "severity": "warning"},
    )
    resp = client.get("/c/billing")
    assert resp.status_code == 200
    assert "UI test" in resp.text
    assert "This message should appear in the stream" in resp.text


def test_unknown_channel_ui_returns_404(client):
    resp = client.get("/c/does-not-exist")
    assert resp.status_code == 404
