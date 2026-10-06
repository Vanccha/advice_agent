import pytest

from privacy.masking import (
    PIILeakError,
    assert_no_pii,
    mask_payload,
    mask_text,
    unmask,
)


def test_mask_text_national_id():
    result = mask_text("NI number: QQ123456C on file.")
    assert "QQ123456C" not in result.masked
    assert "*********" in result.masked


def test_mask_text_phone_keeps_last_two_digits():
    result = mask_text("Phone: +447700900167")
    assert result.masked == "Phone: +44 7*** *** *67"


def test_mask_text_email():
    result = mask_text("E-mail: amelia.jones@example-mail.test")
    assert result.masked == "E-mail: a***@e***.test"


def test_mask_text_iban():
    result = mask_text("IBAN: GB29NWBK60161331926819")
    assert result.masked == "IBAN: GB** **** **** **** **** **"


def test_mask_text_card_number_luhn_valid_only():
    result = mask_text("Card 4111111111111111, order no 1234567890123456")
    assert "**** **** **** 1111" in result.masked
    assert "1234567890123456" in result.masked  # not a card -> left alone


def test_mask_text_mixed_free_text_and_mapping_roundtrip():
    original = "Customer James, tel +447700900167, NI QQ123456C."
    result = mask_text(original)
    assert "+447700900167" not in result.masked
    assert "QQ123456C" not in result.masked
    restored = unmask(result.masked, result.mapping)
    assert restored == original


def test_mask_payload_structured_fields():
    payload = {
        "customer_no": "NS-100042",
        "full_name": "Amy Khan",
        "national_id": "QQ123456C",
        "phone": "+447700900167",
        "email": "amy.khan@example-mail.test",
        "address_line": "3 Fictional Street",
        "district": "Hackney",
        "city": "London",
        "ticket_key": "TKT-2026-00014",
        "nested": {"region_code": "LDN-HAC", "card_number": "4111111111111111"},
    }
    masked = mask_payload(payload)

    assert masked["customer_no"] == "NS-100042"  # allow-listed, untouched
    assert masked["ticket_key"] == "TKT-2026-00014"
    assert masked["full_name"] == "A** K***"
    assert masked["national_id"] == "*********"
    assert masked["phone"] == "+44 7*** *** *67"
    assert masked["email"] == "a***@e***.test"
    assert masked["address_line"] == "Hackney, London"
    assert masked["nested"]["region_code"] == "LDN-HAC"
    assert masked["nested"]["card_number"] == "**** **** **** 1111"


def test_mask_payload_allow_unmasked_survives_in_lists():
    payload = {"affected_customers": ["NS-100042", "NS-100099"]}
    masked = mask_payload(payload)
    assert masked["affected_customers"] == ["NS-100042", "NS-100099"]


def test_assert_no_pii_raises_on_raw_national_id():
    with pytest.raises(PIILeakError):
        assert_no_pii({"observations": ["matched NI number QQ123456C"]})


def test_assert_no_pii_accepts_masked_payload():
    payload = {
        "full_name": "Amy Khan",
        "national_id": "QQ123456C",
        "phone": "+447700900167",
    }
    masked = mask_payload(payload)
    assert_no_pii(masked)  # should not raise
