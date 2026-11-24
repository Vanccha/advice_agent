"""Ticket-specific masking: the bridge from a ticket payload to ``privacy.masking``.

Where this sits: ``tickets`` package; only ``tickets.builder`` calls ``mask_ticket_payload``.

Key ideas:
- Field names differ per payload: the ticket's ``requester`` uses ``name`` / ``contact``, which
  the generic ``mask_payload`` does not know, so those two are masked explicitly first.
- Everything else (subject, body, evidence, steps) goes through the generic sweep, which keeps
  allow-listed ids (``customer_no`` ...) and masks stray PII in free text.
- The requester's name is also passed as a known name, so it is caught inside the body text.

Read next: ``privacy/masking.py``, ``tickets/builder.py``.
See: docs/contracts.md §4.1, §4.7.
"""
from __future__ import annotations

from typing import Any

from privacy.detectors import EMAIL_RE
from privacy.masking import DEFAULT_ALLOW_UNMASKED, mask_email_value, mask_full_name_value, mask_payload, mask_phone_value


def mask_requester_contact(raw_contact: str) -> str:
    """Mask a `contact`, which is either an e-mail or a phone number.

    Contracts §4.7: phone -> `+44 7*** *** *NN`, email -> `a***@d***.com`.
    """
    if EMAIL_RE.fullmatch(raw_contact.strip()):
        return mask_email_value(raw_contact)
    return mask_phone_value(raw_contact)


def mask_requester_name(raw_name: str) -> str:
    """`Ann Lee` -> `A** L**`."""
    return mask_full_name_value(raw_name)


def mask_ticket_payload(
    payload: dict[str, Any],
    *,
    allow_unmasked: frozenset[str] | set[str] | None = None,
    extra_names: list[str] | None = None,
) -> dict[str, Any]:
    """Mask a full ticket payload dict (contracts §4.1 shape).

    Called before the dict is validated into a `StructuredTicket` or sent to the ticketing
    adapter; returns a new dict and leaves the input untouched.
    """
    payload = dict(payload)
    requester = dict(payload.get("requester") or {})

    names_for_sweep = list(extra_names or [])
    raw_name = requester.get("name")
    if raw_name:
        names_for_sweep.append(raw_name)
        requester["name"] = mask_requester_name(raw_name)
    raw_contact = requester.get("contact")
    if raw_contact:
        requester["contact"] = mask_requester_contact(raw_contact)
    payload["requester"] = requester

    allow = allow_unmasked if allow_unmasked is not None else DEFAULT_ALLOW_UNMASKED
    return mask_payload(payload, names=names_for_sweep, allow_unmasked=allow)
