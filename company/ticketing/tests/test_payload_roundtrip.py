from __future__ import annotations

FULL_PAYLOAD = {
    "department": "BILLING",
    "issue_type": "double_charge",
    "priority": "HIGH",
    "subject": "Çift tahsilat — NH-100042 (2 x 459,00 TRY)",
    "body": "<Turkish narrative for the human agent>",
    "source": "api",
    "external_ref": "conv-9f2c1a:double_charge",
    "incident_ref": None,
    "requester": {
        "customer_no": "NH-100042",
        "name": "A** K***",
        "contact": "+90 5** *** ** 31",
    },
    "evidence": {
        "record_ids": {"subscription_id": 42, "payment_ids": [88, 89]},
        "error_codes": [],
        "observations": ["İki ödeme aynı gün, aynı tutar, 4 dakika arayla succeeded."],
        "queried_sources": ["diag.payment_status", "payment_api:list_customer_charges"],
    },
    "attempted_steps": [
        {"step": "get_payment_status", "result": "2 succeeded charges found", "outcome": "info"},
        {
            "step": "policy_check:issue_refund",
            "result": "denied: refund_not_permitted",
            "outcome": "blocked",
        },
    ],
    "affected_customers": ["NH-100042"],
    "suggested_next_step": "88 numaralı ödemenin iadesi (459,00 TRY) onaylanmalı.",
    "urgency_reason": "Müşteriden iki kez tahsilat alındı, yasal süre içinde iade gerekiyor.",
}


def test_structured_payload_round_trips_every_field(client, auth_headers) -> None:
    created = client.post("/api/v1/tickets", json=FULL_PAYLOAD, headers=auth_headers)
    assert created.status_code == 201
    body = created.json()

    fetched = client.get(f"/api/v1/tickets/{body['ticket_key']}", headers=auth_headers)
    assert fetched.status_code == 200
    ticket = fetched.json()

    assert ticket["department"] == FULL_PAYLOAD["department"]
    assert ticket["issue_type"] == FULL_PAYLOAD["issue_type"]
    assert ticket["priority"] == FULL_PAYLOAD["priority"]
    assert ticket["subject"] == FULL_PAYLOAD["subject"]
    assert ticket["body"] == FULL_PAYLOAD["body"]
    assert ticket["source"] == FULL_PAYLOAD["source"]
    assert ticket["external_ref"] == FULL_PAYLOAD["external_ref"]
    assert ticket["incident_ref"] is None

    # requester flattened into requester_customer_no / requester_name / requester_contact
    assert ticket["requester_customer_no"] == FULL_PAYLOAD["requester"]["customer_no"]
    assert ticket["requester_name"] == FULL_PAYLOAD["requester"]["name"]
    assert ticket["requester_contact"] == FULL_PAYLOAD["requester"]["contact"]

    # JSONB fields round-trip exactly, nothing silently dropped
    assert ticket["evidence"] == FULL_PAYLOAD["evidence"]
    assert ticket["attempted_steps"] == FULL_PAYLOAD["attempted_steps"]
    assert ticket["affected_customers"] == FULL_PAYLOAD["affected_customers"]

    assert ticket["suggested_next_step"] == FULL_PAYLOAD["suggested_next_step"]
    assert ticket["urgency_reason"] == FULL_PAYLOAD["urgency_reason"]

    # server-assigned fields
    assert ticket["status"] == "NEW"
    assert ticket["ticket_key"].startswith("TKT-")
    assert ticket["sla_due_at"] is not None
    assert ticket["comments"] == []
    assert len(ticket["status_history"]) == 1
    assert ticket["status_history"][0]["to_status"] == "NEW"
