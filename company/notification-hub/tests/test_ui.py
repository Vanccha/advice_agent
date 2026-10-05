from __future__ import annotations


def test_index_lists_channels_with_turkish_display_names(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Faturalama" in resp.text
    assert "Teknik Altyapı" in resp.text
    assert "Abonelik İşlemleri" in resp.text
    assert "Saha Kurulum Ekibi" in resp.text
    assert "Operasyon Genel" in resp.text


def test_channel_stream_returns_200_and_shows_display_name(client):
    resp = client.get("/c/faturalama")
    assert resp.status_code == 200
    assert "Faturalama" in resp.text
    assert 'content="5"' in resp.text  # auto-refresh meta tag


def test_channel_stream_shows_posted_message(client, api_headers):
    client.post(
        "/api/v1/channels/faturalama/messages",
        headers=api_headers,
        json={"title": "UI testi", "text": "Bu mesaj akışta görünmeli", "severity": "warning"},
    )
    resp = client.get("/c/faturalama")
    assert resp.status_code == 200
    assert "UI testi" in resp.text
    assert "Bu mesaj akışta görünmeli" in resp.text


def test_unknown_channel_ui_returns_404(client):
    resp = client.get("/c/does-not-exist")
    assert resp.status_code == 404
