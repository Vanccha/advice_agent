"""Redaction: the only door out for anything that becomes a trace.

Where this sits: ``observability`` package. ``tracing.py`` passes every payload through
``redact_payload`` / ``redact_metadata`` before it reaches Langfuse or the JSONL fallback; this
is the only observability module that imports ``privacy.masking``, so a payload cannot reach a
trace without masking.

Key ideas:
- Mask, then verify: ``mask_payload`` first, then ``assert_no_pii`` on the result.
- Fail-closed content, fail-open flow: if masking fails or PII still shows, the value is
  replaced by a placeholder and a warning is logged; tracing never blocks a turn.

Read next: ``observability/tracing.py``, ``privacy/masking.py``.
See: docs/contracts.md §4.7, §4.9.
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
