import pytest

from privacy.masking import (
    PIILeakError,
    assert_no_pii,
    mask_payload,
    mask_text,
    unmask,
)


def test_mask_text_national_id():
    result = mask_text("TC kimlik no: 35111111110 kayıtlı.")
    assert "35111111110" not in result.masked
    assert "***********" in result.masked


def test_mask_text_phone_keeps_last_two_digits():
    result = mask_text("Telefon: +905321234567")
    assert result.masked == "Telefon: +90 5** *** ** 67"


def test_mask_text_email():
    result = mask_text("E-posta: ayse.kaya@ornek-eposta.test")
    assert result.masked == "E-posta: a***@o***.test"


def test_mask_text_iban():
    result = mask_text("IBAN: TR330006100519786457841326")
    assert result.masked == "IBAN: TR** **** **** **** **** **** **"


def test_mask_text_card_number_luhn_valid_only():
    result = mask_text("Kart 4111111111111111, sipariş no 1234567890123456")
    assert "**** **** **** 1111" in result.masked
    assert "1234567890123456" in result.masked  # not a card -> left alone


def test_mask_text_mixed_free_text_and_mapping_roundtrip():
    original = "Müşteri Ahmet, tel +905321234567, TC 35111111110."
    result = mask_text(original)
    assert "+905321234567" not in result.masked
    assert "35111111110" not in result.masked
    restored = unmask(result.masked, result.mapping)
    assert restored == original


def test_mask_payload_structured_fields():
    payload = {
        "customer_no": "NH-100042",
        "full_name": "Ali Kaya",
        "national_id": "35111111110",
        "phone": "+905321234567",
        "email": "ali.kaya@ornek-eposta.test",
        "address_line": "Fiktif Sokak No:3",
        "district": "Beşiktaş",
        "city": "İstanbul",
        "ticket_key": "TKT-2026-00014",
        "nested": {"region_code": "IST-BES", "card_number": "4111111111111111"},
    }
    masked = mask_payload(payload)

    assert masked["customer_no"] == "NH-100042"  # allow-listed, untouched
    assert masked["ticket_key"] == "TKT-2026-00014"
    assert masked["full_name"] == "A** K***"
    assert masked["national_id"] == "***********"
    assert masked["phone"] == "+90 5** *** ** 67"
    assert masked["email"] == "a***@o***.test"
    assert masked["address_line"] == "Beşiktaş, İstanbul"
    assert masked["nested"]["region_code"] == "IST-BES"
    assert masked["nested"]["card_number"] == "**** **** **** 1111"


def test_mask_payload_allow_unmasked_survives_in_lists():
    payload = {"affected_customers": ["NH-100042", "NH-100099"]}
    masked = mask_payload(payload)
    assert masked["affected_customers"] == ["NH-100042", "NH-100099"]


def test_assert_no_pii_raises_on_raw_national_id():
    with pytest.raises(PIILeakError):
        assert_no_pii({"observations": ["TC kimlik no 35111111110 ile eşleşti"]})


def test_assert_no_pii_accepts_masked_payload():
    payload = {
        "full_name": "Ali Kaya",
        "national_id": "35111111110",
        "phone": "+905321234567",
    }
    masked = mask_payload(payload)
    assert_no_pii(masked)  # should not raise
