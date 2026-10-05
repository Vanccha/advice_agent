"""The only door out: every payload leaving the process for a trace passes through here.

Contracts §4.7 / §4.9: nothing reaches Langfuse (or the local JSONL fallback) without first
going through ``privacy.masking.mask_payload`` and being verified by
``privacy.masking.assert_no_pii``. To make this impossible to bypass accidentally,
``tracing.py`` never imports ``privacy.masking`` directly and never calls the Langfuse SDK or
``fallback.append_fallback_record`` with anything that did not pass through
``redact_payload``/``redact_metadata`` first.

This module never raises: if masking itself fails, or a masked payload still looks like PII
(a field name the masking rules don't recognise), the offending value is replaced with a
placeholder and a warning is logged once — tracing must never block a conversation turn.
"""
from __future__ import annotations

import logging
from typing import Any

from privacy.masking import PIILeakError, assert_no_pii, mask_payload

logger = logging.getLogger("assistant.observability.redaction")

REDACTED_PLACEHOLDER = "<redacted: masking failed>"


def redact_payload(payload: Any) -> Any:
    """Mask a structured payload (dict/list/str/scalar) and verify no raw PII survived.

    Always returns something safe to emit. Never raises.
    """
    try:
        masked = mask_payload(payload)
    except Exception:
        logger.warning("mask_payload raised while redacting a trace payload; dropping it", exc_info=True)
        return REDACTED_PLACEHOLDER

    try:
        assert_no_pii(masked)
    except PIILeakError:
        logger.warning(
            "masked trace payload still matched a PII pattern after masking; replacing with a placeholder"
        )
        return REDACTED_PLACEHOLDER
    except Exception:
        logger.warning("assert_no_pii raised unexpectedly while verifying a trace payload", exc_info=True)
        return REDACTED_PLACEHOLDER

    return masked


def redact_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Like ``redact_payload`` but guarantees a ``dict`` back (Langfuse ``metadata=`` kwarg
    and the JSONL fallback both expect a mapping)."""
    if not metadata:
        return {}
    redacted = redact_payload(metadata)
    if isinstance(redacted, dict):
        return redacted
    return {"value": redacted}
