from __future__ import annotations


def test_chat_stuck_provisioning_fixed_without_ticket(client, fake_gateway) -> None:
    fake_gateway.set_response(
        "get_subscription_status",
        {
            "customer_no": "NH-100001", "subscription_id": 42, "package_code": "FIBER_100_TEMEL",
            "status": "provisioning", "monthly_price_try": 349.0, "region_code": "IST-KAD",
        },
    )
    fake_gateway.set_response(
        "get_provisioning_status", {"job_id": 7, "status": "stuck", "attempt_count": 1, "is_stuck": True}
    )
    response = client.post(
        "/api/chat",
        json={"message": "İnternetim hala açılmadı, bir arıza var", "customer_no": "NH-100001"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "CLOSING"
    assert body["ticket_key"] is None
    assert body["conversation_id"]


def test_chat_persists_conversation_across_turns(client) -> None:
    first = client.post("/api/chat", json={"message": "Merhaba", "customer_no": "NH-100001"})
    conv_id = first.json()["conversation_id"]
    assert first.json()["mode"] == "CLOSING"

    second = client.post(
        "/api/chat", json={"conversation_id": conv_id, "message": "Paket önerisi istiyorum"}
    )
    assert second.json()["conversation_id"] == conv_id
    assert second.json()["mode"] == "ADVISORY"


def test_chat_stream_emits_token_and_final_events(client) -> None:
    with client.stream(
        "GET", "/api/chat/stream", params={"message": "Merhaba", "conversation_id": ""}
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    assert "event: token" in body
    assert "event: final" in body
