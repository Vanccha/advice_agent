"""Scenario D: the same customer charged twice for the same amount minutes apart.

Where this sits: writes to both the core database (payments) and the payment
gateway's database (charges), so the two systems stay consistent.

Key ideas:
- A realistic duplicate, not an exact copy: the second charge has its own
  reference, which is what makes duplicate detection a genuine exercise.
- Reset removes only the duplicate rows (those it can pair with an earlier
  identical charge inside the time window), leaving the original charge.

Read next: `missed_installation.py`.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from chaos.errors import ChaosError
from chaos.events import record_chaos_event
from chaos.scenarios.common import ResetOutcome, ScenarioOutcome


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
            SELECT p.id AS payment_id, p.subscription_id, p.amount_gbp, p.method,
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
                    (charge_ref, customer_ref, amount_gbp, status, method, idempotency_key,
                     created_at, updated_at)
                VALUES (:ref, :cref, :amount, 'succeeded', :method, :idem, :created, :created)
                """
            ),
            {
                "ref": new_charge_ref, "cref": candidate["customer_no"], "amount": candidate["amount_gbp"],
                "method": candidate["method"], "idem": new_idempotency_key, "created": duplicate_created_at,
            },
        )
    changes.append(f"psp.charges.charge_ref={new_charge_ref}: created, status='succeeded'")

    with core_engine_.begin() as conn:
        payment_id = conn.execute(
            text(
                """
                INSERT INTO core.payments
                    (subscription_id, customer_id, charge_ref, amount_gbp, status, method,
                     idempotency_key, gateway_response, created_at, updated_at)
                VALUES
                    (:sid, :cid, :ref, :amount, 'succeeded', :method, :idem,
                     CAST(:gw AS jsonb), :created, :created)
                RETURNING id
                """
            ),
            {
                "sid": candidate["subscription_id"], "cid": candidate["customer_id"],
                "ref": new_charge_ref, "amount": candidate["amount_gbp"], "method": candidate["method"],
                "idem": new_idempotency_key,
                "gw": f'{{"charge_ref": "{new_charge_ref}", "status": "succeeded"}}',
                "created": duplicate_created_at,
            },
        ).scalar_one()
        changes.append(
            f"core.payments.id={payment_id}: created, status='succeeded', amount={candidate['amount_gbp']} "
            f"GBP, ~4 minutes after payment {candidate['payment_id']}"
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
                   p.amount_gbp, min(p.created_at) AS first_seen, max(p.created_at) AS last_seen
            FROM core.payments p
            JOIN core.subscriptions s ON s.id = p.subscription_id
            JOIN core.customers c ON c.id = s.customer_id
            WHERE p.status = 'succeeded'
            GROUP BY c.customer_no, p.subscription_id, p.amount_gbp
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
                SELECT p.subscription_id, p.amount_gbp, array_agg(p.id ORDER BY p.created_at) AS ids,
                       array_agg(p.charge_ref ORDER BY p.created_at) AS refs
                FROM core.payments p
                WHERE p.status = 'succeeded'
                GROUP BY p.subscription_id, p.amount_gbp
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
