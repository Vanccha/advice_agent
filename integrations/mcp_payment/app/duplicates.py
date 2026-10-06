"""Pure, deterministic duplicate-charge detection — the logic
`detect_duplicate_charges` is built on, kept separate from any HTTP/DB code
so it can be unit-tested with planted data (docs/contracts.md §3: "the
assistant's double-charge diagnosis depends on it").

A duplicate group is two or more `succeeded` charges for the same customer
with the same amount, all within `window_minutes` of the first charge in
the group.
"""
from __future__ import annotations

import datetime
from typing import Any


def _parse_timestamp(value: str) -> datetime.datetime:
    # charges come back as ISO-8601 with a trailing "Z"; datetime.fromisoformat
    # only accepts "+00:00" before Python 3.11, but we're on 3.12.
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def find_duplicate_groups(charges: list[dict[str, Any]], window_minutes: int) -> list[dict[str, Any]]:
    """Group `succeeded` charges by amount, then cluster each amount's charges
    by time: a new cluster starts whenever a charge is more than
    `window_minutes` after the *first* charge of the current cluster. Only
    clusters with 2+ charges are returned (a lone charge isn't a duplicate).
    """
    window = datetime.timedelta(minutes=window_minutes)

    by_amount: dict[float, list[dict[str, Any]]] = {}
    for charge in charges:
        if charge.get("status") != "succeeded":
            continue
        amount = round(float(charge["amount_gbp"]), 2)
        by_amount.setdefault(amount, []).append(charge)

    groups: list[dict[str, Any]] = []
    for amount, same_amount_charges in by_amount.items():
        ordered = sorted(same_amount_charges, key=lambda c: c["created_at"])
        cluster: list[dict[str, Any]] = []
        cluster_anchor: datetime.datetime | None = None

        def flush() -> None:
            if len(cluster) >= 2:
                groups.append(
                    {
                        "amount_gbp": amount,
                        "charge_refs": [c["charge_ref"] for c in cluster],
                        "timestamps": [c["created_at"] for c in cluster],
                        "count": len(cluster),
                    }
                )

        for charge in ordered:
            ts = _parse_timestamp(charge["created_at"])
            if cluster_anchor is not None and ts - cluster_anchor <= window:
                cluster.append(charge)
            else:
                flush()
                cluster = [charge]
                cluster_anchor = ts
        flush()

    groups.sort(key=lambda g: g["timestamps"][0])
    return groups
