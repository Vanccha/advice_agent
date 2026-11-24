"""Scenario E: an installation visit that did not happen.

Where this sits: marks an appointment as missed, leaving the subscription in
`installation_scheduled`.

Key ideas:
- The subscription status does not change, so only the appointment record
  reveals the problem; diagnosis must look at more than the headline status.
- The `MissedInstallations` alert reads this state.

Read next: `payment_down.py`.
"""
from __future__ import annotations

import random
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from shared.clock import utcnow

from chaos.errors import ChaosError
from chaos.events import record_chaos_event
from chaos.scenarios.common import ResetOutcome, ScenarioOutcome


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
