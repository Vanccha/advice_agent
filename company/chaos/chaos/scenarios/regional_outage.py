"""Scenario C: a critical regional incident with affected customers.

Where this sits: writes an incident and links the region's subscriptions to it.

Key ideas:
- One incident for many customers: the expected response is a single ticket
  for the whole incident, not one per customer.
- The open critical incident drives the `RegionalOutageDetected` alert.
- Incident numbers continue the existing numbering, so injected incidents look
  like real ones.

Read next: `double_charge.py`.
"""
from __future__ import annotations

import random
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from shared.clock import utcnow

from chaos.errors import ChaosError
from chaos.events import record_chaos_event
from chaos.scenarios.common import ResetOutcome, ScenarioOutcome


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
            "<=£5 goodwill credit with explicit confirmation."
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
