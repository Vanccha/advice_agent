"""UK GDPR masking at the process boundary: mask before anything leaves the assistant.

Where this sits: ``privacy`` package. Called by ``modes`` before building a model prompt, by
``tickets`` and ``observability`` before writing out, and by ``llm.guard`` and ``audit.log``
as a backstop. It uses the finders in ``detectors.py``. The file has three parts: value
renderers, free-text masking (``mask_text``) and structured masking (``mask_payload``, driven
by field names); they stay together because they share the same renderers.

Key ideas:
- Mask before egress: model providers, traces and tickets only ever see masked data.
- Two strategies: regex/validator-driven for free text, field-name-driven for structured
  payloads. Neither guesses at arbitrary words.
- Allow-list of IDs that must stay readable (``customer_no`` ...): a masked ticket key is
  useless to a department agent.
- ``assert_no_pii`` is the fail-closed backstop that raises ``PIILeakError`` instead of leaking.

Read next: ``privacy/detectors.py``, ``llm/guard.py``.
See: docs/contracts.md §4.7.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from privacy.detectors import (
    find_card_numbers,
    find_emails,
    find_full_names,
    find_ibans,
    find_national_ids,
    find_phones,
)

# --- Result types ------------------------------------------------------------


class MaskResult(BaseModel):
    """Masked text plus `masked value -> original`, so a reply can be unmasked later."""

    model_config = ConfigDict(frozen=True)

    masked: str
    mapping: dict[str, str] = Field(default_factory=dict)


class PIILeakError(ValueError):
    """Raised by `assert_no_pii` when raw PII is found where only masked data is allowed."""


# Field names treated as PII in a structured payload (contracts §4.6 `pii.mask_before_model`).
# "address" is special: it is replaced by its `district`/`city` siblings when present.
DEFAULT_PII_FIELD_NAMES = frozenset(
    {"national_id", "phone", "email", "iban", "card_number", "full_name", "address", "address_line"}
)

# Fields that must survive masking untouched — a masked customer_no is useless to a
# department agent (contracts §4.6 `pii.allow_unmasked`).
DEFAULT_ALLOW_UNMASKED = frozenset(
    {"customer_no", "subscription_id", "ticket_key", "region_code", "package_code"}
)


# --- Per-type mask renderers -------------------------------------------------


def mask_national_id_value() -> str:
    return "*" * 9


def mask_phone_value(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    last2 = digits[-2:] if len(digits) >= 2 else "??"
    return f"+44 7*** *** *{last2}"


def mask_email_value(raw: str) -> str:
    local, sep, domain = raw.partition("@")
    if not sep:
        return "***"
    local_masked = f"{local[0]}***" if local else "***"
    domain_first, dot, domain_rest = domain.partition(".")
    domain_masked = f"{domain_first[0]}***" if domain_first else "***"
    return f"{local_masked}@{domain_masked}.{domain_rest}" if dot else f"{local_masked}@{domain_masked}"


def mask_iban_value() -> str:
    return "GB** **** **** **** **** **"


def mask_card_value(digits: str) -> str:
    last4 = digits[-4:] if len(digits) >= 4 else digits
    return f"**** **** **** {last4}"


def mask_full_name_value(raw: str) -> str:
    words = raw.split(" ")
    masked_words = [w[0] + "*" * (len(w) - 1) if len(w) > 1 else w for w in words]
    return " ".join(masked_words)


# --- Free-text masking -------------------------------------------------------

_Finder = Callable[[str], list[tuple[int, int, str]]]
_Masker = Callable[[str, str], str]


def _apply_masker(text: str, finder: _Finder, masker: _Masker) -> tuple[str, dict[str, str]]:
    """Replace every span `finder` reports with `masker(...)`; returns text and the mapping."""
    spans = finder(text)
    if not spans:
        return text, {}
    mapping: dict[str, str] = {}
    parts: list[str] = []
    last_end = 0
    for start, end, cleaned in spans:
        if start < last_end:
            continue  # overlapping match from a greedy pattern — keep the first
        original = text[start:end]
        masked_value = masker(cleaned, original)
        parts.append(text[last_end:start])
        parts.append(masked_value)
        mapping[masked_value] = original
        last_end = end
    parts.append(text[last_end:])
    return "".join(parts), mapping


def mask_text(text: str, names: list[str] | None = None) -> MaskResult:
    """Mask every detectable PII pattern in free text.

    `names` is an optional explicit list of known full names to redact (never a pattern
    guess). The returned mapping lets the caller `unmask` text that comes back later.
    """
    mapping: dict[str, str] = {}
    current = text

    # Order matters: email/iban/card first (most specific), then phone, then national id
    # (an NI number always starts with two letters, so it can never overlap a phone
    # number's digit run — see detectors.py).
    for finder, masker in (
        (find_emails, lambda cleaned, raw: mask_email_value(raw)),
        (find_ibans, lambda cleaned, raw: mask_iban_value()),
        (find_card_numbers, lambda cleaned, raw: mask_card_value(cleaned)),
        (find_phones, lambda cleaned, raw: mask_phone_value(raw)),
        (find_national_ids, lambda cleaned, raw: mask_national_id_value()),
    ):
        current, step_mapping = _apply_masker(current, finder, masker)
        mapping.update(step_mapping)

    if names:
        current, step_mapping = _apply_masker(
            current,
            lambda t: find_full_names(t, names),
            lambda cleaned, raw: mask_full_name_value(raw),
        )
        mapping.update(step_mapping)

    return MaskResult(masked=current, mapping=mapping)


def unmask(text: str, mapping: dict[str, str]) -> str:
    """Reverse of `mask_text`'s substitutions, using the mapping it returned."""
    result = text
    for masked_value in sorted(mapping, key=len, reverse=True):
        result = result.replace(masked_value, mapping[masked_value])
    return result


# --- Structured payload masking ----------------------------------------------


def _mask_scalar_field(key: str, value: Any, node: dict[str, Any]) -> Any:
    """Masked value for a recognised PII field name, else ``None``.

    ``None`` also covers non-string values, in which case the caller recurses into them.
    """
    if not isinstance(value, str):
        return None
    lower_key = key.lower()
    if lower_key == "national_id":
        return mask_national_id_value()
    if lower_key == "phone":
        return mask_phone_value(value)
    if lower_key == "email":
        return mask_email_value(value)
    if lower_key == "iban":
        return mask_iban_value()
    if lower_key == "card_number":
        digits = re.sub(r"\D", "", value)
        return mask_card_value(digits) if digits else value
    if lower_key == "full_name":
        return mask_full_name_value(value)
    if lower_key in ("address", "address_line"):
        district = node.get("district")
        city = node.get("city")
        if district and city:
            return f"{district}, {city}"
        return "<address hidden>"
    return None


def _mask_node(node: Any, allow_unmasked: frozenset[str], names: list[str]) -> Any:
    """Recursively mask a payload: by field name first, `mask_text` for other strings."""
    if isinstance(node, dict):
        result: dict[str, Any] = {}
        for key, value in node.items():
            if key in allow_unmasked:
                result[key] = value
                continue
            rendered = _mask_scalar_field(key, value, node)
            if rendered is not None:
                result[key] = rendered
            else:
                result[key] = _mask_node(value, allow_unmasked, names)
        return result
    if isinstance(node, list):
        return [_mask_node(item, allow_unmasked, names) for item in node]
    if isinstance(node, str):
        return mask_text(node, names=names).masked
    return node


def mask_payload(
    obj: Any,
    *,
    names: list[str] | None = None,
    allow_unmasked: frozenset[str] | set[str] | None = None,
) -> Any:
    """Mask a structured payload (dict/list/str/...), same shape back.

    Field names drive masking (`national_id`, `phone`, `email`, `iban`, `card_number`,
    `full_name`, `address`/`address_line`); `allow_unmasked` field names always survive;
    any other string value is passed through `mask_text`.
    """
    allow = frozenset(allow_unmasked) if allow_unmasked is not None else DEFAULT_ALLOW_UNMASKED
    return _mask_node(obj, allow, names or [])


# --- Leak guard --------------------------------------------------------------


def _iter_strings(node: Any) -> Any:
    """Yield every string nested anywhere inside dicts, lists and tuples."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _iter_strings(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _iter_strings(value)


def assert_no_pii(obj: Any) -> None:
    """Raise `PIILeakError` if anything in `obj` still looks like raw PII.

    The fail-closed backstop used at the model boundary (`llm.guard`) and the audit-log
    boundary (`AuditLog.append`): data handed over must already be masked. Names are not
    checked here, since they can only be found with an explicit list.
    """
    for text in _iter_strings(obj):
        if (
            find_national_ids(text)
            or find_phones(text)
            or find_emails(text)
            or find_ibans(text)
            or find_card_numbers(text)
        ):
            raise PIILeakError(f"raw PII detected: {text!r}")
