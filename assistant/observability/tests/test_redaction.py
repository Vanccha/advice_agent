from __future__ import annotations

import json

import pytest

from observability import redaction


def test_dict_payload_with_raw_pii_is_masked():
    payload = {
        "full_name": "Ahmet Yilmaz",
        "national_id": "QQ123456C",
        "phone": "+447700900167",
        "email": "ahmet.yilmaz@example-mail.test",
        "customer_no": "NS-100042",
    }
    safe = redaction.redact_payload(payload)

    assert safe["national_id"] == "*" * 9
    assert safe["phone"] != "+447700900167"
    assert safe["phone"].startswith("+44 7***")
    assert "@" in safe["email"] and safe["email"] != "ahmet.yilmaz@example-mail.test"
    assert safe["full_name"] != "Ahmet Yilmaz"
    # customer_no is explicitly allow-listed (contracts §4.6 pii.allow_unmasked)
    assert safe["customer_no"] == "NS-100042"

    serialized = json.dumps(safe, ensure_ascii=False)
    assert "QQ123456C" not in serialized
    assert "+447700900167" not in serialized
    assert "ahmet.yilmaz@example-mail.test" not in serialized


def test_free_text_pii_embedded_in_a_string_is_masked():
    text = "The customer's phone is +447700900167, NI number QQ123456C."
    safe = redaction.redact_payload(text)
    assert "+447700900167" not in safe
    assert "QQ123456C" not in safe


def test_redact_metadata_always_returns_a_dict():
    assert redaction.redact_metadata(None) == {}
    assert redaction.redact_metadata({}) == {}
    wrapped = redaction.redact_metadata({"a": "normal text, no pii"})
    assert isinstance(wrapped, dict)


def test_redact_payload_never_raises_even_when_masking_breaks(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated masking failure")

    monkeypatch.setattr(redaction, "mask_payload", _boom)
    result = redaction.redact_payload({"phone": "+447700900167"})
    assert result == redaction.REDACTED_PLACEHOLDER


def test_redact_payload_falls_back_to_placeholder_if_leak_guard_still_fires(monkeypatch):
    # Simulate mask_payload leaving something that still looks like PII (e.g. a field name
    # the masking rules don't recognise) — assert_no_pii must still catch it and redaction
    # must still never raise or leak the raw value out.
    monkeypatch.setattr(redaction, "mask_payload", lambda obj: {"note": "NI number QQ123456C"})
    result = redaction.redact_payload({"note": "NI number QQ123456C"})
    assert result == redaction.REDACTED_PLACEHOLDER
