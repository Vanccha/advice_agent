from __future__ import annotations

import json

import pytest

from observability import redaction


def test_dict_payload_with_raw_pii_is_masked():
    payload = {
        "full_name": "Ahmet Yilmaz",
        "national_id": "12345678901",
        "phone": "+905551234567",
        "email": "ahmet.yilmaz@ornek-eposta.test",
        "customer_no": "NH-100042",
    }
    safe = redaction.redact_payload(payload)

    assert safe["national_id"] == "*" * 11
    assert safe["phone"] != "+905551234567"
    assert safe["phone"].startswith("+90 5**")
    assert "@" in safe["email"] and safe["email"] != "ahmet.yilmaz@ornek-eposta.test"
    assert safe["full_name"] != "Ahmet Yilmaz"
    # customer_no is explicitly allow-listed (contracts §4.6 pii.allow_unmasked)
    assert safe["customer_no"] == "NH-100042"

    serialized = json.dumps(safe, ensure_ascii=False)
    assert "12345678901" not in serialized
    assert "+905551234567" not in serialized
    assert "ahmet.yilmaz@ornek-eposta.test" not in serialized


def test_free_text_pii_embedded_in_a_string_is_masked():
    text = "Müşterinin telefonu +905551234567, kimlik no 12345678901."
    safe = redaction.redact_payload(text)
    assert "+905551234567" not in safe
    assert "12345678901" not in safe


def test_redact_metadata_always_returns_a_dict():
    assert redaction.redact_metadata(None) == {}
    assert redaction.redact_metadata({}) == {}
    wrapped = redaction.redact_metadata({"a": "normal text, no pii"})
    assert isinstance(wrapped, dict)


def test_redact_payload_never_raises_even_when_masking_breaks(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated masking failure")

    monkeypatch.setattr(redaction, "mask_payload", _boom)
    result = redaction.redact_payload({"phone": "+905551234567"})
    assert result == redaction.REDACTED_PLACEHOLDER


def test_redact_payload_falls_back_to_placeholder_if_leak_guard_still_fires(monkeypatch):
    # Simulate mask_payload leaving something that still looks like PII (e.g. a field name
    # the masking rules don't recognise) — assert_no_pii must still catch it and redaction
    # must still never raise or leak the raw value out.
    monkeypatch.setattr(redaction, "mask_payload", lambda obj: {"note": "kimlik no 12345678901"})
    result = redaction.redact_payload({"note": "kimlik no 12345678901"})
    assert result == redaction.REDACTED_PLACEHOLDER
