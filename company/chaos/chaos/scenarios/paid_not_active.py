"""Scenario B: a subscription that is paid but stranded before provisioning.

Where this sits: reuses the candidate helpers from `common.py` and
`stuck_provisioning.py` (via `ensure_candidate`).

Key ideas:
- The fault is a state the real system can reach (payment received, no job),
  so diagnosis tools see exactly what they would see in production.
- Detection is a plain query, so `status` can report it without bookkeeping.

Read next: `regional_outage.py`.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from shared.clock import utcnow

from chaos.events import record_chaos_event
from chaos.scenarios.common import ResetOutcome, ScenarioOutcome, customer_row, latest_subscription


def _pick_paid_not_active(conn: Connection, customer_no: str | None, region: str | None) -> dict | None:
    if customer_no:
        cust = customer_row(conn, customer_no)
        sub = latest_subscription(conn, cust["customer_id"])
        if sub["status"] != "payment_received":
            return None
        return {"subscription_id": sub["id"], "customer_no": cust["customer_no"], "region_code": cust["region_code"]}

    region_filter = "AND c.region_code = :region" if region else ""
    params: dict[str, Any] = {"region": region} if region else {}
    row = conn.execute(
        text(
            f"""
            SELECT s.id AS subscription_id, c.customer_no, c.region_code
            FROM core.subscriptions s
            JOIN core.customers c ON c.id = s.customer_id
            WHERE s.status = 'payment_received'
              AND EXISTS (
                  SELECT 1 FROM core.provisioning_jobs j
                  WHERE j.subscription_id = s.id AND j.status IN ('queued', 'running')
              )
              {region_filter}
            ORDER BY random() LIMIT 1
            """
        ),
        params,
    ).mappings().first()
    return dict(row) if row is not None else None


def find_paid_not_active_candidate(engine: Engine, customer_no: str | None, region: str | None) -> dict | None:
    """Pure read: an existing subscription already suitable for paid_not_active, if any."""
    with engine.connect() as conn:
        return _pick_paid_not_active(conn, customer_no, region)


def apply_paid_not_active(engine: Engine, candidate: dict) -> ScenarioOutcome:
    sub_id = candidate["subscription_id"]
    changes: list[str] = []
    deleted_job_ids: list[int] = []
    with engine.begin() as conn:
        jobs = conn.execute(
            text(
                "SELECT id FROM core.provisioning_jobs WHERE subscription_id = :sid "
                "AND status IN ('queued', 'running')"
            ),
            {"sid": sub_id},
        ).mappings().all()
        for job in jobs:
            conn.execute(text("DELETE FROM core.provisioning_jobs WHERE id = :jid"), {"jid": job["id"]})
            deleted_job_ids.append(job["id"])
            changes.append(f"provisioning_jobs.id={job['id']}: deleted (queued job that should have existed)")

        record_chaos_event(
            conn,
            subscription_id=sub_id,
            event_type="chaos_injected",
            reason="Payment succeeded but the provisioning job never got queued.",
            payload={"scenario": "paid_not_active", "deleted_job_ids": deleted_job_ids},
        )

    return ScenarioOutcome(
        scenario="paid_not_active",
        dry_run=False,
        picked={"customer_no": candidate["customer_no"], "subscription_id": sub_id,
                "region_code": candidate["region_code"]},
        changes=changes,
        expected_behavior=(
            "Subscription is paid but stranded before provisioning. Expected: diagnose, "
            "then queue/retry provisioning and resend the activation notification -- no "
            "ticket needed unless a refund is requested."
        ),
        records={"subscription_id": sub_id},
    )


def detect_paid_not_active(conn: Connection) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT s.id AS subscription_id, c.customer_no, s.updated_at
            FROM core.subscriptions s
            JOIN core.customers c ON c.id = s.customer_id
            WHERE s.status = 'payment_received'
              AND NOT EXISTS (
                  SELECT 1 FROM core.provisioning_jobs j
                  WHERE j.subscription_id = s.id AND j.status IN ('queued', 'running')
              )
            ORDER BY s.id
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def reset_paid_not_active(engine: Engine) -> ResetOutcome:
    changes: list[str] = []
    created_job_ids: list[int] = []
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT s.id AS subscription_id
                FROM core.subscriptions s
                WHERE s.status = 'payment_received'
                  AND NOT EXISTS (
                      SELECT 1 FROM core.provisioning_jobs j
                      WHERE j.subscription_id = s.id AND j.status IN ('queued', 'running')
                  )
                """
            )
        ).mappings().all()
        now = utcnow()
        for row in rows:
            job_id = conn.execute(
                text(
                    """
                    INSERT INTO core.provisioning_jobs
                        (subscription_id, status, attempt_count, max_attempts, queued_at, created_at, updated_at)
                    VALUES (:sid, 'queued', 0, 3, :now, :now, :now)
                    RETURNING id
                    """
                ),
                {"sid": row["subscription_id"], "now": now},
            ).scalar_one()
            created_job_ids.append(job_id)
            record_chaos_event(
                conn,
                subscription_id=row["subscription_id"],
                event_type="chaos_reset",
                reason="Missing provisioning job re-queued.",
                payload={"scenario": "paid_not_active", "job_id": job_id},
            )
            changes.append(
                f"provisioning_jobs.id={job_id}: created status='queued' for subscription {row['subscription_id']}"
            )
    return ResetOutcome(scenario="paid_not_active", changes=changes, records={"job_ids": created_job_ids})
