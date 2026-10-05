from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from shared.clock import utcnow

from chaos.errors import ChaosError
from chaos.events import record_chaos_event
from chaos.http import get_control_flags, pay_subscription_until_succeeded, set_control_flags
from chaos.settings import ChaosSettings

# ---------------------------------------------------------------------------
# Shared result types
# ---------------------------------------------------------------------------


@dataclass
class ScenarioOutcome:
    scenario: str
    dry_run: bool
    picked: dict[str, Any]
    changes: list[str]
    expected_behavior: str
    records: dict[str, Any] = field(default_factory=dict)


@dataclass
class StatusEntry:
    scenario: str
    active: bool
    records: list[dict[str, Any]]


@dataclass
class ResetOutcome:
    scenario: str
    changes: list[str]
    records: dict[str, Any] = field(default_factory=dict)


def _region_label(conn: Connection, region_code: str) -> str:
    row = conn.execute(
        text("SELECT name, city FROM core.regions WHERE code = :code"), {"code": region_code}
    ).mappings().first()
    if row is None:
        return region_code
    return f"{row['name']}, {row['city']} ({region_code})"


# ---------------------------------------------------------------------------
# a) stuck_provisioning
# ---------------------------------------------------------------------------


def _customer_row(conn: Connection, customer_no: str) -> dict:
    cust = conn.execute(
        text(
            "SELECT c.id AS customer_id, c.customer_no, c.region_code "
            "FROM core.customers c WHERE c.customer_no = :no"
        ),
        {"no": customer_no},
    ).mappings().first()
    if cust is None:
        raise ChaosError(f"Customer '{customer_no}' not found.")
    return dict(cust)


def _latest_subscription(conn: Connection, customer_id: int) -> dict:
    sub = conn.execute(
        text(
            "SELECT id, status FROM core.subscriptions WHERE customer_id = :cid "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"cid": customer_id},
    ).mappings().first()
    if sub is None:
        raise ChaosError("Customer has no subscription.")
    return dict(sub)


def _find_promotable_subscription(conn: Connection, customer_no: str | None, region: str | None) -> dict | None:
    """A 'registered'/'awaiting_payment' subscription that the real payments API
    can drive forward into 'payment_received' (used when no seeded victim is left
    in a later-stage status -- the live worker and prior chaos runs both consume
    that pool over time)."""
    if customer_no:
        cust = _customer_row(conn, customer_no)
        sub = _latest_subscription(conn, cust["customer_id"])
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
        cust = _customer_row(conn, customer_no)
        sub = _latest_subscription(conn, cust["customer_id"])
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


def promote_to_payment_received(settings: ChaosSettings, candidate: dict) -> dict:
    """Mutating: drive `candidate` to 'payment_received' via the real payments API."""
    pay_subscription_until_succeeded(settings, candidate["subscription_id"])
    candidate["status"] = "payment_received"
    return candidate


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


# ---------------------------------------------------------------------------
# b) paid_not_active
# ---------------------------------------------------------------------------


def _pick_paid_not_active(conn: Connection, customer_no: str | None, region: str | None) -> dict | None:
    if customer_no:
        cust = _customer_row(conn, customer_no)
        sub = _latest_subscription(conn, cust["customer_id"])
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


# ---------------------------------------------------------------------------
# c) regional_outage
# ---------------------------------------------------------------------------

_INCIDENT_TITLES = [
    "OLT uplink fault on the core distribution ring",
    "Fiber cut on the regional backhaul",
    "Power loss at the aggregation node",
]
_INCIDENT_DESCRIPTIONS = [
    "An uplink fault on the regional OLT ring has taken down service for "
    "subscribers served by this node. Field teams are investigating.",
    "A fiber cut on the backhaul link serving this region has interrupted "
    "service. A field crew has been dispatched to the affected segment.",
    "The aggregation node lost mains power and has not yet failed over to "
    "backup; subscribers downstream are offline.",
]


def _pick_region(conn: Connection, region: str | None) -> dict:
    if region:
        row = conn.execute(
            text("SELECT code, name, city FROM core.regions WHERE code = :code"), {"code": region}
        ).mappings().first()
        if row is None:
            raise ChaosError(f"Region '{region}' not found.")
        return dict(row)

    row = conn.execute(
        text(
            """
            SELECT r.code, r.name, r.city, count(s.id) AS active_count
            FROM core.regions r
            JOIN core.customers c ON c.region_code = r.code
            JOIN core.subscriptions s ON s.customer_id = c.id AND s.status = 'active'
            WHERE NOT EXISTS (
                SELECT 1 FROM core.network_incidents ni
                WHERE ni.region_code = r.code AND ni.status IN ('open', 'monitoring')
            )
            GROUP BY r.code, r.name, r.city
            ORDER BY active_count DESC
            LIMIT 1
            """
        )
    ).mappings().first()
    if row is None:
        raise ChaosError("No region with active subscriptions and no open incident was found.")
    return dict(row)


def pick_regional_outage(engine: Engine, region: str | None) -> dict:
    with engine.connect() as conn:
        return _pick_region(conn, region)


def _next_incident_no(conn: Connection) -> str:
    last = conn.execute(
        text("SELECT incident_no FROM core.network_incidents ORDER BY id DESC LIMIT 1")
    ).scalar()
    year = utcnow().year
    if last:
        try:
            n = int(last.rsplit("-", 1)[-1]) + 1
        except ValueError:
            n = 1
    else:
        n = 1
    return f"INC-{year}-{n:03d}"


def apply_regional_outage(engine: Engine, region_row: dict) -> ScenarioOutcome:
    region_code = region_row["code"]
    rng = random.Random()
    changes: list[str] = []

    with engine.begin() as conn:
        incident_no = _next_incident_no(conn)
        now = utcnow()
        title = rng.choice(_INCIDENT_TITLES)
        description = rng.choice(_INCIDENT_DESCRIPTIONS)
        eta = now + timedelta(hours=rng.choice([2, 3, 4]))

        affected = conn.execute(
            text(
                """
                SELECT s.id AS subscription_id, c.id AS customer_id, c.customer_no
                FROM core.subscriptions s
                JOIN core.customers c ON c.id = s.customer_id
                WHERE c.region_code = :region AND s.status = 'active'
                """
            ),
            {"region": region_code},
        ).mappings().all()

        incident_id = conn.execute(
            text(
                """
                INSERT INTO core.network_incidents
                    (incident_no, region_code, severity, status, title, description,
                     started_at, estimated_resolution_at, affected_subscription_count,
                     created_at, updated_at)
                VALUES
                    (:no, :region, 'critical', 'open', :title, :description,
                     :now, :eta, :count, :now, :now)
                RETURNING id
                """
            ),
            {
                "no": incident_no, "region": region_code, "title": title,
                "description": description, "now": now, "eta": eta, "count": len(affected),
            },
        ).scalar_one()
        changes.append(
            f"network_incidents.incident_no={incident_no}: created (critical, open, "
            f"region={region_code}, affected={len(affected)})"
        )

        modems_flipped = 0
        for row in affected:
            conn.execute(
                text(
                    "INSERT INTO core.incident_subscriptions (incident_id, subscription_id) "
                    "VALUES (:iid, :sid) ON CONFLICT DO NOTHING"
                ),
                {"iid": incident_id, "sid": row["subscription_id"]},
            )
            result = conn.execute(
                text(
                    "UPDATE core.modems SET status = 'offline' "
                    "WHERE subscription_id = :sid AND status != 'offline'"
                ),
                {"sid": row["subscription_id"]},
            )
            modems_flipped += result.rowcount or 0
            record_chaos_event(
                conn,
                subscription_id=row["subscription_id"],
                event_type="chaos_injected",
                reason=f"Linked to regional incident {incident_no}; modem marked offline.",
                payload={"scenario": "regional_outage", "incident_no": incident_no},
            )

        changes.append(
            f"modems: {modems_flipped} modem(s) in region {region_code} set to 'offline'"
        )

    region_label = f"{region_row['name']}, {region_row['city']} ({region_code})"
    return ScenarioOutcome(
        scenario="regional_outage",
        dry_run=False,
        picked={"region_code": region_code, "region_label": region_label, "incident_no": incident_no},
        changes=changes,
        expected_behavior=(
            f"{len(affected)} customers are affected. Expected: attach every affected "
            f"customer to incident {incident_no}, open at most one ticket for the whole "
            "incident (not one per customer), inform customers, and optionally offer a "
            "<=50 TL goodwill credit with explicit confirmation."
        ),
        records={"incident_no": incident_no, "affected_subscription_count": len(affected)},
    )


def detect_regional_outage(conn: Connection) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT incident_no, region_code, severity, status, affected_subscription_count, started_at
            FROM core.network_incidents
            WHERE status IN ('open', 'monitoring')
            ORDER BY id
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def reset_regional_outage(engine: Engine) -> ResetOutcome:
    changes: list[str] = []
    incident_nos: list[str] = []
    with engine.begin() as conn:
        incidents = conn.execute(text("SELECT id, incident_no FROM core.network_incidents")).mappings().all()
        for inc in incidents:
            subs = conn.execute(
                text("SELECT subscription_id FROM core.incident_subscriptions WHERE incident_id = :iid"),
                {"iid": inc["id"]},
            ).mappings().all()
            restored = 0
            for s in subs:
                result = conn.execute(
                    text(
                        "UPDATE core.modems SET status = 'online' "
                        "WHERE subscription_id = :sid AND status = 'offline'"
                    ),
                    {"sid": s["subscription_id"]},
                )
                restored += result.rowcount or 0
                record_chaos_event(
                    conn,
                    subscription_id=s["subscription_id"],
                    event_type="chaos_reset",
                    reason=f"Incident {inc['incident_no']} resolved; modem restored online.",
                    payload={"scenario": "regional_outage", "incident_no": inc["incident_no"]},
                )
            conn.execute(
                text("DELETE FROM core.incident_subscriptions WHERE incident_id = :iid"),
                {"iid": inc["id"]},
            )
            conn.execute(text("DELETE FROM core.network_incidents WHERE id = :iid"), {"iid": inc["id"]})
            changes.append(
                f"network_incidents.incident_no={inc['incident_no']}: deleted "
                f"({restored} modem(s) restored online)"
            )
            incident_nos.append(inc["incident_no"])
    return ResetOutcome(scenario="regional_outage", changes=changes, records={"incident_nos": incident_nos})


# ---------------------------------------------------------------------------
# d) double_charge
# ---------------------------------------------------------------------------


def _pick_double_charge(conn: Connection, customer_no: str | None, region: str | None) -> dict:
    if customer_no:
        filter_sql = "AND c.customer_no = :customer_no"
        params: dict[str, Any] = {"customer_no": customer_no}
    elif region:
        filter_sql = "AND c.region_code = :region"
        params = {"region": region}
    else:
        filter_sql = ""
        params = {}

    row = conn.execute(
        text(
            f"""
            SELECT p.id AS payment_id, p.subscription_id, p.amount_try, p.method,
                   p.charge_ref, p.created_at, c.customer_no, c.region_code, c.id AS customer_id
            FROM core.payments p
            JOIN core.subscriptions s ON s.id = p.subscription_id
            JOIN core.customers c ON c.id = s.customer_id
            WHERE p.status = 'succeeded'
              AND (SELECT count(*) FROM core.payments p2
                   WHERE p2.subscription_id = p.subscription_id AND p2.status = 'succeeded') = 1
              {filter_sql}
            ORDER BY random() LIMIT 1
            """
        ),
        params,
    ).mappings().first()
    if row is None:
        scope = f" for {customer_no}" if customer_no else (f" in region {region}" if region else "")
        raise ChaosError(f"No subscription with exactly one succeeded payment found{scope}.")
    return dict(row)


def pick_double_charge(engine: Engine, customer_no: str | None, region: str | None) -> dict:
    with engine.connect() as conn:
        return _pick_double_charge(conn, customer_no, region)


def apply_double_charge(core_engine_: Engine, payment_engine_: Engine, candidate: dict) -> ScenarioOutcome:
    changes: list[str] = []
    new_charge_ref = f"ch_{uuid.uuid4().hex[:20]}"
    new_idempotency_key = f"idem-{uuid.uuid4().hex}"
    duplicate_created_at = candidate["created_at"] + timedelta(minutes=4)

    with payment_engine_.begin() as pconn:
        pconn.execute(
            text(
                """
                INSERT INTO psp.charges
                    (charge_ref, customer_ref, amount_try, status, method, idempotency_key,
                     created_at, updated_at)
                VALUES (:ref, :cref, :amount, 'succeeded', :method, :idem, :created, :created)
                """
            ),
            {
                "ref": new_charge_ref, "cref": candidate["customer_no"], "amount": candidate["amount_try"],
                "method": candidate["method"], "idem": new_idempotency_key, "created": duplicate_created_at,
            },
        )
    changes.append(f"psp.charges.charge_ref={new_charge_ref}: created, status='succeeded'")

    with core_engine_.begin() as conn:
        payment_id = conn.execute(
            text(
                """
                INSERT INTO core.payments
                    (subscription_id, customer_id, charge_ref, amount_try, status, method,
                     idempotency_key, gateway_response, created_at, updated_at)
                VALUES
                    (:sid, :cid, :ref, :amount, 'succeeded', :method, :idem,
                     CAST(:gw AS jsonb), :created, :created)
                RETURNING id
                """
            ),
            {
                "sid": candidate["subscription_id"], "cid": candidate["customer_id"],
                "ref": new_charge_ref, "amount": candidate["amount_try"], "method": candidate["method"],
                "idem": new_idempotency_key,
                "gw": f'{{"charge_ref": "{new_charge_ref}", "status": "succeeded"}}',
                "created": duplicate_created_at,
            },
        ).scalar_one()
        changes.append(
            f"core.payments.id={payment_id}: created, status='succeeded', amount={candidate['amount_try']} "
            f"TRY, ~4 minutes after payment {candidate['payment_id']}"
        )
        record_chaos_event(
            conn,
            subscription_id=candidate["subscription_id"],
            event_type="chaos_injected",
            reason="A retry with a new idempotency key produced a second successful charge.",
            payload={
                "scenario": "double_charge",
                "original_payment_id": candidate["payment_id"],
                "duplicate_payment_id": payment_id,
                "duplicate_charge_ref": new_charge_ref,
            },
        )

    return ScenarioOutcome(
        scenario="double_charge",
        dry_run=False,
        picked={"customer_no": candidate["customer_no"], "subscription_id": candidate["subscription_id"]},
        changes=changes,
        expected_behavior=(
            "Two succeeded charges for the same customer/amount ~4 minutes apart. "
            "Expected: detect the duplicate, recognise a refund is not a permitted "
            "self-service action, and open a BILLING ticket referencing both payment ids."
        ),
        records={"original_payment_id": candidate["payment_id"], "duplicate_payment_id": payment_id,
                  "duplicate_charge_ref": new_charge_ref},
    )


def detect_double_charge(conn: Connection, window_minutes: int = 30) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT c.customer_no, p.subscription_id, array_agg(p.id ORDER BY p.created_at) AS payment_ids,
                   p.amount_try, min(p.created_at) AS first_seen, max(p.created_at) AS last_seen
            FROM core.payments p
            JOIN core.subscriptions s ON s.id = p.subscription_id
            JOIN core.customers c ON c.id = s.customer_id
            WHERE p.status = 'succeeded'
            GROUP BY c.customer_no, p.subscription_id, p.amount_try
            HAVING count(*) > 1
               AND (max(p.created_at) - min(p.created_at)) <= make_interval(mins => :window)
            ORDER BY c.customer_no
            """
        ),
        {"window": window_minutes},
    ).mappings().all()
    return [dict(r) for r in rows]


def reset_double_charge(engine: Engine, payment_engine_: Engine, window_minutes: int = 30) -> ResetOutcome:
    changes: list[str] = []
    removed_payment_ids: list[int] = []
    removed_charge_refs: list[str] = []
    with engine.begin() as conn:
        groups = conn.execute(
            text(
                """
                SELECT p.subscription_id, p.amount_try, array_agg(p.id ORDER BY p.created_at) AS ids,
                       array_agg(p.charge_ref ORDER BY p.created_at) AS refs
                FROM core.payments p
                WHERE p.status = 'succeeded'
                GROUP BY p.subscription_id, p.amount_try
                HAVING count(*) > 1
                   AND (max(p.created_at) - min(p.created_at)) <= make_interval(mins => :window)
                """
            ),
            {"window": window_minutes},
        ).mappings().all()
        for group in groups:
            ids = group["ids"]
            refs = group["refs"]
            # Keep the earliest payment, delete the rest.
            for pid, ref in zip(ids[1:], refs[1:]):
                conn.execute(text("DELETE FROM core.payments WHERE id = :pid"), {"pid": pid})
                removed_payment_ids.append(pid)
                if ref:
                    removed_charge_refs.append(ref)
                changes.append(f"core.payments.id={pid}: deleted (duplicate of payment {ids[0]})")
    if removed_charge_refs:
        with payment_engine_.begin() as pconn:
            for ref in removed_charge_refs:
                pconn.execute(text("DELETE FROM psp.charges WHERE charge_ref = :ref"), {"ref": ref})
                changes.append(f"psp.charges.charge_ref={ref}: deleted")
    return ResetOutcome(
        scenario="double_charge", changes=changes,
        records={"removed_payment_ids": removed_payment_ids, "removed_charge_refs": removed_charge_refs},
    )


# ---------------------------------------------------------------------------
# e) missed_installation
# ---------------------------------------------------------------------------


def _pick_missed_installation(conn: Connection, customer_no: str | None, region: str | None) -> dict:
    if customer_no:
        filter_sql = "AND c.customer_no = :customer_no"
        params: dict[str, Any] = {"customer_no": customer_no}
    elif region:
        filter_sql = "AND c.region_code = :region"
        params = {"region": region}
    else:
        filter_sql = ""
        params = {}

    row = conn.execute(
        text(
            f"""
            SELECT a.id AS appointment_id, a.subscription_id, a.team_code, a.time_slot,
                   c.customer_no, c.region_code
            FROM core.installation_appointments a
            JOIN core.subscriptions s ON s.id = a.subscription_id
            JOIN core.customers c ON c.id = s.customer_id
            WHERE a.status = 'scheduled' AND s.status = 'installation_scheduled'
              {filter_sql}
            ORDER BY random() LIMIT 1
            """
        ),
        params,
    ).mappings().first()
    if row is None:
        scope = f" for {customer_no}" if customer_no else (f" in region {region}" if region else "")
        raise ChaosError(f"No scheduled installation appointment found{scope}.")
    return dict(row)


def pick_missed_installation(engine: Engine, customer_no: str | None, region: str | None) -> dict:
    with engine.connect() as conn:
        return _pick_missed_installation(conn, customer_no, region)


def apply_missed_installation(engine: Engine, candidate: dict) -> ScenarioOutcome:
    appt_id = candidate["appointment_id"]
    now = utcnow()
    yesterday = (now - timedelta(days=1)).date()
    changes: list[str] = []
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE core.installation_appointments
                SET scheduled_date = :yesterday, status = 'missed',
                    technician_note = 'Customer not present at the address during the visit window.',
                    updated_at = :now
                WHERE id = :aid
                """
            ),
            {"yesterday": yesterday, "now": now, "aid": appt_id},
        )
        changes.append(
            f"installation_appointments.id={appt_id}: scheduled_date={yesterday}, status -> 'missed'"
        )
        record_chaos_event(
            conn,
            subscription_id=candidate["subscription_id"],
            event_type="chaos_injected",
            reason="Field technician could not complete the scheduled installation visit.",
            payload={"scenario": "missed_installation", "appointment_id": appt_id},
        )
    return ScenarioOutcome(
        scenario="missed_installation",
        dry_run=False,
        picked={"customer_no": candidate["customer_no"], "subscription_id": candidate["subscription_id"],
                "appointment_id": appt_id, "team_code": candidate["team_code"]},
        changes=changes,
        expected_behavior=(
            "The subscription stays 'installation_scheduled' with a missed visit. Expected: "
            "open a FIELD_INSTALL ticket referencing the appointment id and team code -- no "
            "self-service reschedule."
        ),
        records={"appointment_id": appt_id},
    )


def detect_missed_installation(conn: Connection) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT a.id AS appointment_id, a.subscription_id, c.customer_no, a.scheduled_date, a.team_code
            FROM core.installation_appointments a
            JOIN core.subscriptions s ON s.id = a.subscription_id
            JOIN core.customers c ON c.id = s.customer_id
            WHERE a.status = 'missed'
            ORDER BY a.id
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def reset_missed_installation(engine: Engine) -> ResetOutcome:
    changes: list[str] = []
    appt_ids: list[int] = []
    rng = random.Random()
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, subscription_id FROM core.installation_appointments WHERE status = 'missed'")
        ).mappings().all()
        now = utcnow()
        for row in rows:
            new_date = (now + timedelta(days=rng.randint(3, 10))).date()
            conn.execute(
                text(
                    """
                    UPDATE core.installation_appointments
                    SET status = 'scheduled', scheduled_date = :new_date,
                        technician_note = NULL, updated_at = :now
                    WHERE id = :aid
                    """
                ),
                {"new_date": new_date, "now": now, "aid": row["id"]},
            )
            record_chaos_event(
                conn,
                subscription_id=row["subscription_id"],
                event_type="chaos_reset",
                reason="Installation visit rebooked.",
                payload={"scenario": "missed_installation", "appointment_id": row["id"]},
            )
            changes.append(f"installation_appointments.id={row['id']}: 'missed' -> 'scheduled' ({new_date})")
            appt_ids.append(row["id"])
    return ResetOutcome(scenario="missed_installation", changes=changes, records={"appointment_ids": appt_ids})


# ---------------------------------------------------------------------------
# f) payment_down
# ---------------------------------------------------------------------------


def apply_payment_down(settings: ChaosSettings) -> ScenarioOutcome:
    flags = set_control_flags(settings, outage=True)
    return ScenarioOutcome(
        scenario="payment_down",
        dry_run=False,
        picked={},
        changes=[f"psp.control_flags.outage: -> true (POST /psp/v1/control)"],
        expected_behavior=(
            "Prometheus must fire PaymentGatewayDown (up==0 or psp_outage==1, for 1m) -> "
            "Alertmanager -> the integration webhook. Expected: proactive diagnosis, a "
            "TECHNICAL_INFRA ticket, a department channel message, and users told the "
            "payment system is temporarily unavailable."
        ),
        records={"control_flags": flags},
    )


def detect_payment_down(settings: ChaosSettings) -> dict:
    flags = get_control_flags(settings)
    return flags


def reset_payment_down(settings: ChaosSettings) -> ResetOutcome:
    flags = set_control_flags(
        settings, outage=False, force_failure_code=None, failure_rate=settings.psp_failure_rate
    )
    return ResetOutcome(
        scenario="payment_down",
        changes=[
            f"psp.control_flags: outage=false, force_failure_code=null, "
            f"failure_rate={settings.psp_failure_rate} (POST /psp/v1/control)"
        ],
        records={"control_flags": flags},
    )
