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
    """Append a `core.subscription_events` row with actor='chaos'.

    This is the audit trail required by the failure-injection spec: every record
    chaos creates or mutates must be traceable back to a scenario run. `actor`
    is already a first-class value in the `subscription_events` check constraint
    (system, csr, api_client, job, chaos) -- this is not a new marker, it is the
    documented way for this tool to leave a trace.
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
