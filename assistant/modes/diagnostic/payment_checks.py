"""Reading payment evidence: duplicate charges and a payment gateway that is not serving.

Where this sits: `modes.diagnostic`, helper module. `checklist.py` calls these to interpret
the raw tool payloads from the payment and monitoring adapters.

Key ideas:
- Adapter payloads come from another system and have changed shape over time, so every
  reader is defensive: it accepts the known spellings and returns "nothing found" rather
  than raising on an unexpected one.
- Two independent signals back each conclusion (the PSP-side detector and the core payment
  records; monitoring and the gateway's own health), because each can miss what the other
  sees.

Read next: modes/diagnostic/checklist.py.
"""
from __future__ import annotations

from typing import Any

# --- Duplicate charges --------------------------------------------------------------
def extract_duplicate_pairs(data: Any) -> list[list[int]]:
    """``detect_duplicate_charges`` (live `mcp-payment`) returns
    ``{"customer_no": ..., "window_minutes": 60, "duplicate_groups": [...]}``; fixture-based
    tests commonly spell the same idea as ``{"duplicates": [...]}`` or ``{"items": [...]}``.
    Each group is expected to carry its charge/payment ids under one of a few plausible
    key names — handled defensively since this is data from another agent's adapter."""
    groups: Any = data
    if isinstance(data, dict):
        groups = (
            data.get("duplicate_groups")
            or data.get("duplicates")
            or data.get("items")
            or []
        )
    if not isinstance(groups, list):
        return []
    pairs: list[list[int]] = []
    for group in groups:
        if isinstance(group, dict):
            ids = (
                group.get("payment_ids")
                or group.get("charge_ids")
                or group.get("ids")
                or group.get("payments")
            )
            if isinstance(ids, list) and len(ids) >= 2:
                pairs.append(list(ids))
        elif isinstance(group, list) and len(group) >= 2:
            pairs.append(list(group))
    return pairs


def duplicate_pairs_from_payment_records(records: list[dict[str, Any]]) -> list[list[int]]:
    """Secondary duplicate-charge signal: two or more succeeded payments of the same amount.

    Covers data the PSP-side detector cannot see, e.g. a seeded first payment that was only
    mirrored into core's ledger and never into the gateway's.
    """
    succeeded = [r for r in records if r.get("status") == "succeeded" and r.get("payment_id") is not None]
    by_amount: dict[Any, list[int]] = {}
    for record in succeeded:
        by_amount.setdefault(record.get("amount_gbp"), []).append(record["payment_id"])
    return [ids for ids in by_amount.values() if len(ids) >= 2]


# --- Payment gateway health ---------------------------------------------------------
_DOWN_WORDS = ("down", "unavailable", "outage", "unhealthy")


def payment_gateway_down(health: dict[str, Any]) -> bool:
    """True when the monitoring view says the payment gateway is not serving.

    The live adapter answers `{"services": [{"service": "payment-gateway", "status": "down",
    "reason": ...}], ...}`; fixtures and earlier shapes used a mapping or a flat record, so
    all of them are accepted.
    """
    services = health.get("services")
    if isinstance(services, list):
        for entry in services:
            if not isinstance(entry, dict):
                continue
            if "payment" not in str(entry.get("service", "")).lower():
                continue
            if str(entry.get("status", "")).lower() in _DOWN_WORDS:
                return True

    candidates = [health]
    if isinstance(services, dict):
        candidates.append(services)
    for node in candidates:
        for key, value in node.items():
            if "payment" not in str(key).lower():
                continue
            if isinstance(value, dict) and value.get("ok") is False:
                return True
            if isinstance(value, str) and value.lower() in _DOWN_WORDS:
                return True
    if health.get("ok") is False and "payment" in str(health.get("service", "")).lower():
        return True
    return False
