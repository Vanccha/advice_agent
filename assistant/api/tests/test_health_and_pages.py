from __future__ import annotations


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_metrics(client) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200


def test_index_renders_widget(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="chat-widget"' in response.text
    assert 'id="chat-form"' in response.text


def test_login_page_renders(client) -> None:
    response = client.get("/login")
    assert response.status_code == 200
    assert 'id="login-form"' in response.text
