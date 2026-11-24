"""Audit trail for injected faults.

Where this sits: called by scenarios whenever they change a subscription.

Key ideas:
- Every record a scenario creates or mutates leaves a
  `subscription_events` row with `actor='chaos'`, so an injected fault can
  always be told apart from organic data, and a reset can find it again.

Read next: `scenarios/stuck_provisioning.py`.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from shared.clock import utcnow


def record_chaos_event(
    conn: Connection,
    *,
    subscription_id: int,
    event_type: str,
    reason: str,
    payload: dict[str, Any],
) -> None:
    """Append a `core.subscription_events` row with actor `chaos`.

    This is the audit trail: every record chaos creates or mutates must be
    traceable to a scenario run. `chaos` is already an allowed `actor` value in
    the table's check constraint, so no new marker is needed.
    """
    status = conn.execute(
        text("SELECT status FROM core.subscriptions WHERE id = :sid"),
        {"sid": subscription_id},
    ).scalar_one()
    conn.execute(
        text(
            """
            INSERT INTO core.subscription_events
                (subscription_id, event_type, from_status, to_status, actor, reason, payload, created_at)
            VALUES
                (:sid, :event_type, :status, :status, 'chaos', :reason, CAST(:payload AS jsonb), :now)
            """
        ),
        {
            "sid": subscription_id,
            "event_type": event_type,
            "status": status,
            "reason": reason,
            "payload": json.dumps(payload),
            "now": utcnow(),
        },
    )
