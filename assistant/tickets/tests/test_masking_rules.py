from tickets.masking_rules import mask_requester_contact, mask_requester_name, mask_ticket_payload


def test_mask_requester_name_to_initials():
    assert mask_requester_name("Ali Kaya") == "A** K***"


def test_mask_requester_contact_detects_phone():
    assert mask_requester_contact("+90 532 111 22 31") == "+90 5** *** ** 31"


def test_mask_requester_contact_detects_email():
    assert mask_requester_contact("ali.kaya@ornek-eposta.test") == "a***@o***.test"


def test_mask_ticket_payload_keeps_allow_unmasked_fields_intact():
    payload = {
        "department": "BILLING",
        "requester": {
            "customer_no": "NH-100042",
            "name": "Ali Kaya",
            "contact": "+90 532 111 22 31",
        },
        "evidence": {"record_ids": {"subscription_id": 42}},
    }
    masked = mask_ticket_payload(payload)
    assert masked["requester"]["customer_no"] == "NH-100042"
    assert masked["requester"]["name"] == "A** K***"
    assert masked["requester"]["contact"] == "+90 5** *** ** 31"
    assert masked["evidence"]["record_ids"]["subscription_id"] == 42


def test_mask_ticket_payload_catches_pii_restated_in_free_text():
    payload = {
        "requester": {"customer_no": "NH-1", "name": "Ali Kaya", "contact": "+90 532 111 22 31"},
        "body": "Müşteri Ali Kaya, 0532 111 22 31 numarasından ulaşılabilir.",
    }
    masked = mask_ticket_payload(payload)
    assert "Ali Kaya" not in masked["body"]
    assert "0532 111 22 31" not in masked["body"]
