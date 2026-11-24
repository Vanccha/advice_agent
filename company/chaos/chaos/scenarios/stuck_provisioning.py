"""Scenario A: a provisioning job frozen mid-run.

Where this sits: also home of the candidate-finding helpers other scenarios
reuse (`ensure_candidate`, `promote_to_payment_received`, `create_fresh_signup`).

Key ideas:
- A `CHAOS_HOLD` marker on the job tells the real worker's sweeper to leave it
  alone, so the fault stays in place until reset.
- Candidate selection prefers existing data, then promotes an early-lifecycle
  subscription through the real payments API, and as a last resort registers a
  brand-new customer, so the scenario works on a depleted dataset.
- The job's heartbeat is back-dated, which is what the `is_stuck` diagnostic
  view and the `StuckProvisioningJobs` alert key off.

Read next: `paid_not_active.py`.
"""
from __future__ import annotations

import random
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from shared.clock import utcnow
from shared.errors import ApiError
from shared.fake_identity import (
    make_address,
    make_email,
    make_full_name,
    make_invalid_national_id,
    make_phone,
)

from chaos.errors import ChaosError
from chaos.events import record_chaos_event
from chaos.http import core_api_client, pay_subscription_until_succeeded
from chaos.settings import ChaosSettings
from chaos.scenarios.common import ResetOutcome, ScenarioOutcome, customer_row, latest_subscription
from chaos.scenarios.paid_not_active import find_paid_not_active_candidate


def _find_promotable_subscription(conn: Connection, customer_no: str | None, region: str | None) -> dict | None:
    """A 'registered'/'awaiting_payment' subscription that the real payments API
    can drive forward into 'payment_received' (used when no seeded victim is left
    in a later-stage status -- the live worker and prior chaos runs both consume
    that pool over time)."""
    if customer_no:
        cust = customer_row(conn, customer_no)
        sub = latest_subscription(conn, cust["customer_id"])
        if sub["status"] not in ("registered", "awaiting_payment"):
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
            WHERE s.status IN ('registered', 'awaiting_payment') {region_filter}
            ORDER BY random() LIMIT 1
            """
        ),
        params,
    ).mappings().first()
    return dict(row) if row is not None else None


def _pick_stuck_provisioning(conn: Connection, customer_no: str | None, region: str | None) -> dict | None:
    if customer_no:
        cust = customer_row(conn, customer_no)
        sub = latest_subscription(conn, cust["customer_id"])
        if sub["status"] not in ("provisioning", "payment_received"):
            return None
        return {
            "subscription_id": sub["id"],
            "status": sub["status"],
            "customer_no": cust["customer_no"],
            "region_code": cust["region_code"],
        }

    region_filter = "AND c.region_code = :region" if region else ""
    params: dict[str, Any] = {"region": region} if region else {}

    # Prefer a subscription already 'provisioning' with a job to freeze.
    row = conn.execute(
        text(
            f"""
            SELECT s.id AS subscription_id, s.status, c.customer_no, c.region_code
            FROM core.subscriptions s
            JOIN core.customers c ON c.id = s.customer_id
            WHERE s.status = 'provisioning' {region_filter}
              AND NOT EXISTS (
                  SELECT 1 FROM core.provisioning_jobs j
                  WHERE j.subscription_id = s.id AND j.last_error_message = 'CHAOS_HOLD'
              )
            ORDER BY random() LIMIT 1
            """
        ),
        params,
    ).mappings().first()
    if row is not None:
        return dict(row)

    # Fallback: a 'payment_received' subscription we can advance into provisioning.
    row = conn.execute(
        text(
            f"""
            SELECT s.id AS subscription_id, s.status, c.customer_no, c.region_code
            FROM core.subscriptions s
            JOIN core.customers c ON c.id = s.customer_id
            WHERE s.status = 'payment_received' {region_filter}
            ORDER BY random() LIMIT 1
            """
        ),
        params,
    ).mappings().first()
    return dict(row) if row is not None else None


def find_stuck_provisioning_candidate(
    engine: Engine, customer_no: str | None, region: str | None
) -> dict | None:
    """Pure read: an existing subscription already suitable for stuck_provisioning, if any."""
    with engine.connect() as conn:
        return _pick_stuck_provisioning(conn, customer_no, region)


def find_promotable_subscription(engine: Engine, customer_no: str | None, region: str | None) -> dict | None:
    """Pure read: a 'registered'/'awaiting_payment' subscription that could be promoted."""
    with engine.connect() as conn:
        return _find_promotable_subscription(conn, customer_no, region)


FRESH_SIGNUP_PACKAGE = "FIBER_100_BASIC"


def create_fresh_signup(settings: ChaosSettings, engine: Engine, region: str | None = None) -> dict:
    """Register a brand-new customer and subscription through the company's own API.

    The seeded pool of early-stage subscriptions is finite: the provisioning worker drains
    it and every injection consumes one, so after a while the early-lifecycle scenarios
    would have no victim left and simply fail — including during a live demo. Rather than
    give up, this does exactly what a real signup does (`POST /v1/customers` then
    `POST /v1/subscriptions`), which keeps those scenarios reproducible indefinitely.
    """
    rng = random.Random()
    with engine.connect() as conn:
        if region:
            row = conn.execute(
                text("SELECT code FROM core.regions WHERE code = :code"), {"code": region}
            ).mappings().first()
            if row is None:
                raise ChaosError(f"unknown region {region!r}")
            region_code = row["code"]
        else:
            row = conn.execute(
                text("SELECT code FROM core.regions ORDER BY random() LIMIT 1")
            ).mappings().first()
            if row is None:
                raise ChaosError("the company has no regions configured")
            region_code = row["code"]
        district_row = conn.execute(
            text("SELECT name, city FROM core.regions WHERE code = :code"), {"code": region_code}
        ).mappings().first()

    full_name = make_full_name(rng)
    client = core_api_client(settings)
    try:
        customer = client.post(
            "/v1/customers",
            json_body={
                "full_name": full_name,
                "national_id": make_invalid_national_id(rng),
                "phone": make_phone(rng),
                "email": make_email(full_name, rng),
                "address_line": make_address(rng),
                "district": district_row["name"] if district_row else "Merkez",
                "city": district_row["city"] if district_row else "London",
                "region_code": region_code,
                "gdpr_consent": True,
            },
        ).json()
        subscription = client.post(
            "/v1/subscriptions",
            json_body={"customer_no": customer["customer_no"], "package_code": FRESH_SIGNUP_PACKAGE},
        ).json()
    except ApiError as exc:
        raise ChaosError(f"could not register a new subscriber through core-api: {exc}") from exc

    return {
        "subscription_id": subscription["id"],
        "customer_no": customer["customer_no"],
        "region_code": region_code,
        "status": subscription["status"],
        "fresh_signup": True,
    }


def promote_to_payment_received(settings: ChaosSettings, candidate: dict) -> dict:
    """Mutating: drive `candidate` to 'payment_received' via the real payments API."""
    pay_subscription_until_succeeded(settings, candidate["subscription_id"])
    candidate["status"] = "payment_received"
    return candidate


def ensure_candidate(
    settings: ChaosSettings,
    engine: Engine,
    *,
    kind: str,
    customer_no: str | None = None,
    region: str | None = None,
) -> tuple[dict, bool]:
    """Resolve a victim for an early-lifecycle scenario, creating one if need be.

    Returns `(candidate, needs_promotion)`. `kind` is "stuck_provisioning" or
    "paid_not_active". The three-step resolution — an already-suitable subscription, then
    one the real payments API can drive to `payment_received`, then a fresh signup — lives
    here so the CLI and the tests cannot disagree about it. When `customer_no` is given the
    caller asked for a specific subscriber, so no substitute is invented.
    """
    finder = (
        find_stuck_provisioning_candidate if kind == "stuck_provisioning"
        else find_paid_not_active_candidate
    )
    candidate = finder(engine, customer_no, region)
    if candidate is not None:
        return candidate, False

    candidate = find_promotable_subscription(engine, customer_no, region)
    if candidate is not None:
        return candidate, True

    if customer_no is not None:
        raise ChaosError(
            f"{customer_no} is not in a state this scenario can use, and a specific "
            "subscriber was requested, so no substitute was created."
        )
    return create_fresh_signup(settings, engine, region), True


def apply_stuck_provisioning(engine: Engine, candidate: dict) -> ScenarioOutcome:
    sub_id = candidate["subscription_id"]
    now = utcnow()
    frozen_heartbeat = now - timedelta(minutes=30)
    changes: list[str] = []

    with engine.begin() as conn:
        if candidate["status"] == "payment_received":
            conn.execute(
                text("UPDATE core.subscriptions SET status = 'provisioning', updated_at = :now WHERE id = :sid"),
                {"now": now, "sid": sub_id},
            )
            changes.append(
                f"subscriptions.id={sub_id}: status 'payment_received' -> 'provisioning'"
            )

        job = conn.execute(
            text(
                "SELECT id, status FROM core.provisioning_jobs WHERE subscription_id = :sid "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"sid": sub_id},
        ).mappings().first()

        if job is None:
            job_id = conn.execute(
                text(
                    """
                    INSERT INTO core.provisioning_jobs
                        (subscription_id, status, attempt_count, max_attempts,
                         queued_at, started_at, heartbeat_at, last_error_message,
                         created_at, updated_at)
                    VALUES
                        (:sid, 'running', 0, 3, :frozen, :frozen, :frozen, 'CHAOS_HOLD', :now, :now)
                    RETURNING id
                    """
                ),
                {"sid": sub_id, "frozen": frozen_heartbeat, "now": now},
            ).scalar_one()
            changes.append(
                f"provisioning_jobs.id={job_id}: created status='running', "
                f"heartbeat_at=now-30m, last_error_message='CHAOS_HOLD'"
            )
        else:
            job_id = job["id"]
            conn.execute(
                text(
                    """
                    UPDATE core.provisioning_jobs
                    SET status = 'running', started_at = COALESCE(started_at, :frozen),
                        heartbeat_at = :frozen, finished_at = NULL,
                        last_error_code = NULL, last_error_message = 'CHAOS_HOLD',
                        updated_at = :now
                    WHERE id = :jid
                    """
                ),
                {"frozen": frozen_heartbeat, "now": now, "jid": job_id},
            )
            changes.append(
                f"provisioning_jobs.id={job_id}: status -> 'running', "
                f"heartbeat_at=now-30m, last_error_message='CHAOS_HOLD' (was '{job['status']}')"
            )

        record_chaos_event(
            conn,
            subscription_id=sub_id,
            event_type="chaos_injected",
            reason="Provisioning job frozen mid-run by the failure-injection tool.",
            payload={"scenario": "stuck_provisioning", "job_id": job_id},
        )

    return ScenarioOutcome(
        scenario="stuck_provisioning",
        dry_run=False,
        picked={"customer_no": candidate["customer_no"], "subscription_id": sub_id,
                "region_code": candidate["region_code"]},
        changes=changes,
        expected_behavior=(
            "The provisioning worker must leave this job alone (CHAOS_HOLD marker). "
            "Expected: diagnose the stuck job, call retry_provisioning_job (policy allows "
            "it), and close out without opening a ticket."
        ),
        records={"job_id": job_id, "subscription_id": sub_id},
    )


def detect_stuck_provisioning(conn: Connection) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT j.id AS job_id, j.subscription_id, c.customer_no, j.heartbeat_at, j.status
            FROM core.provisioning_jobs j
            JOIN core.subscriptions s ON s.id = j.subscription_id
            JOIN core.customers c ON c.id = s.customer_id
            WHERE j.status = 'running' AND j.last_error_message = 'CHAOS_HOLD'
            ORDER BY j.id
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def reset_stuck_provisioning(engine: Engine) -> ResetOutcome:
    changes: list[str] = []
    job_ids: list[int] = []
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, subscription_id FROM core.provisioning_jobs "
                "WHERE status = 'running' AND last_error_message = 'CHAOS_HOLD'"
            )
        ).mappings().all()
        now = utcnow()
        for row in rows:
            conn.execute(
                text(
                    """
                    UPDATE core.provisioning_jobs
                    SET status = 'queued', last_error_code = NULL, last_error_message = NULL,
                        started_at = NULL, finished_at = NULL, heartbeat_at = NULL,
                        queued_at = :now, updated_at = :now
                    WHERE id = :jid
                    """
                ),
                {"now": now, "jid": row["id"]},
            )
            record_chaos_event(
                conn,
                subscription_id=row["subscription_id"],
                event_type="chaos_reset",
                reason="Provisioning hold released; job returned to the normal queue.",
                payload={"scenario": "stuck_provisioning", "job_id": row["id"]},
            )
            changes.append(f"provisioning_jobs.id={row['id']}: 'running'(CHAOS_HOLD) -> 'queued'")
            job_ids.append(row["id"])
    return ResetOutcome(scenario="stuck_provisioning", changes=changes, records={"job_ids": job_ids})
