"""Ticket-specific bridge onto `privacy.masking` (contracts §4.1 / §4.7).

The structured ticket payload's `requester` object uses the field names `name`/`contact`
(not the generic `full_name`/`phone`/`email` names `privacy.masking.mask_payload` keys
off of), so those two fields are masked explicitly here. Everything else in the ticket
(subject, body, evidence, attempted steps, ...) then goes through the generic
`privacy.masking.mask_payload` sweep, which keeps the tenant's `pii.allow_unmasked` field
names (`customer_no`, `subscription_id`, `ticket_key`, `region_code`, `package_code`)
intact and free-text-masks any stray PII anywhere else in the payload.

Import as: ``from tickets.masking_rules import mask_ticket_payload``.
"""
from __future__ import annotations

from typing import Any

from privacy.detectors import EMAIL_RE
from privacy.masking import DEFAULT_ALLOW_UNMASKED, mask_email_value, mask_full_name_value, mask_payload, mask_phone_value


def mask_requester_contact(raw_contact: str) -> str:
    """`contact` is either a phone or an email; detect which and mask with the matching
    rule (contracts §4.7: phone -> `+90 5** *** ** NN`, email -> `a***@d***.com`)."""
    if EMAIL_RE.fullmatch(raw_contact.strip()):
        return mask_email_value(raw_contact)
    return mask_phone_value(raw_contact)


def mask_requester_name(raw_name: str) -> str:
    return mask_full_name_value(raw_name)


def mask_ticket_payload(
    payload: dict[str, Any],
    *,
    allow_unmasked: frozenset[str] | set[str] | None = None,
    extra_names: list[str] | None = None,
) -> dict[str, Any]:
    """Mask a full ticket payload dict (contracts §4.1 shape) before it is validated into
    a `tickets.builder.StructuredTicket` / sent to the ticketing adapter."""
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
