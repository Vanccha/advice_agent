from tickets.masking_rules import mask_requester_contact, mask_requester_name, mask_ticket_payload


def test_mask_requester_name_to_initials():
    assert mask_requester_name("Amy Khan") == "A** K***"


def test_mask_requester_contact_detects_phone():
    assert mask_requester_contact("+44 7700 900 131") == "+44 7*** *** *31"


def test_mask_requester_contact_detects_email():
    assert mask_requester_contact("amy.khan@example-mail.test") == "a***@e***.test"


def test_mask_ticket_payload_keeps_allow_unmasked_fields_intact():
    payload = {
        "department": "BILLING",
        "requester": {
            "customer_no": "NS-100042",
            "name": "Amy Khan",
            "contact": "+44 7700 900 131",
        },
        "evidence": {"record_ids": {"subscription_id": 42}},
    }
    masked = mask_ticket_payload(payload)
    assert masked["requester"]["customer_no"] == "NS-100042"
    assert masked["requester"]["name"] == "A** K***"
    assert masked["requester"]["contact"] == "+44 7*** *** *31"
    assert masked["evidence"]["record_ids"]["subscription_id"] == 42


def test_mask_ticket_payload_catches_pii_restated_in_free_text():
    payload = {
        "requester": {"customer_no": "NS-1", "name": "Amy Khan", "contact": "+44 7700 900 131"},
        "body": "Customer Amy Khan can be reached on 07700 900 131.",
    }
    masked = mask_ticket_payload(payload)
    assert "Amy Khan" not in masked["body"]
    assert "07700 900 131" not in masked["body"]
