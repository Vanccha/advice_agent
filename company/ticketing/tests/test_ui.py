from __future__ import annotations


def _create_ticket(client, auth_headers, ref: str) -> dict:
    payload = {
        "department": "FIELD_INSTALL",
        "issue_type": "missed_installation",
        "priority": "NORMAL",
        "subject": "Kaçırılan kurulum randevusu",
        "body": "Müşteri evde değildi.",
        "source": "web",
        "external_ref": ref,
    }
    resp = client.post("/api/v1/tickets", json=payload, headers=auth_headers)
    assert resp.status_code == 201
    return resp.json()


def test_agent_list_page_renders_with_turkish_labels(client, auth_headers) -> None:
    ticket = _create_ticket(client, auth_headers, "ui-list-test")

    resp = client.get("/agent")
    assert resp.status_code == 200
    html = resp.text
    assert ticket["ticket_key"] in html
    assert "Talepler" in html
    assert "Saha Kurulum Ekibi" in html
    assert "Departman" in html


def test_agent_detail_page_renders_with_turkish_labels(client, auth_headers) -> None:
    ticket = _create_ticket(client, auth_headers, "ui-detail-test")

    resp = client.get(f"/agent/tickets/{ticket['ticket_key']}")
    assert resp.status_code == 200
    html = resp.text
    assert ticket["ticket_key"] in html
    assert "Talep Eden" in html
    assert "Durum Geçmişi" in html
    assert "Yorumlar" in html


def test_agent_list_filters_by_department(client, auth_headers) -> None:
    _create_ticket(client, auth_headers, "ui-filter-test")
    resp = client.get("/agent", params={"department": "FIELD_INSTALL"})
    assert resp.status_code == 200
    resp_other = client.get("/agent", params={"department": "BILLING"})
    assert resp_other.status_code == 200
