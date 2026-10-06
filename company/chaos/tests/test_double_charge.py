from __future__ import annotations

from sqlalchemy import text
from typer.testing import CliRunner

from chaos import scenarios as sc
from chaos.cli import app

runner = CliRunner()


def test_inject_creates_matching_charge_and_payment_rows(core_eng, payment_eng):
    candidate = sc.pick_double_charge(core_eng, None, None)
    outcome = sc.apply_double_charge(core_eng, payment_eng, candidate)

    dup_payment_id = outcome.records["duplicate_payment_id"]
    dup_charge_ref = outcome.records["duplicate_charge_ref"]

    with core_eng.connect() as conn:
        payment = conn.execute(
            text(
                "SELECT subscription_id, amount_gbp, status, charge_ref, created_at FROM core.payments "
                "WHERE id = :id"
            ),
            {"id": dup_payment_id},
        ).mappings().one()
        original = conn.execute(
            text("SELECT amount_gbp, created_at FROM core.payments WHERE id = :id"),
            {"id": candidate["payment_id"]},
        ).mappings().one()

    with payment_eng.connect() as conn:
        charge = conn.execute(
            text("SELECT customer_ref, amount_gbp, status FROM psp.charges WHERE charge_ref = :ref"),
            {"ref": dup_charge_ref},
        ).mappings().one()

    assert payment["status"] == "succeeded"
    assert float(payment["amount_gbp"]) == float(original["amount_gbp"])
    assert payment["subscription_id"] == candidate["subscription_id"]
    delta = payment["created_at"] - original["created_at"]
    assert abs(delta.total_seconds() - 240) < 5

    assert charge["status"] == "succeeded"
    assert float(charge["amount_gbp"]) == float(original["amount_gbp"])
    assert charge["customer_ref"] == candidate["customer_no"]


def test_status_detects_duplicate_by_amount_and_window(core_eng, payment_eng):
    with core_eng.connect() as conn:
        assert sc.detect_double_charge(conn) == []

    candidate = sc.pick_double_charge(core_eng, None, None)
    sc.apply_double_charge(core_eng, payment_eng, candidate)

    with core_eng.connect() as conn:
        detected = sc.detect_double_charge(conn)
    assert any(d["subscription_id"] == candidate["subscription_id"] and len(d["payment_ids"]) == 2 for d in detected)


def test_reset_removes_the_duplicate_only_and_is_idempotent(core_eng, payment_eng):
    candidate = sc.pick_double_charge(core_eng, None, None)
    outcome = sc.apply_double_charge(core_eng, payment_eng, candidate)
    dup_payment_id = outcome.records["duplicate_payment_id"]
    dup_charge_ref = outcome.records["duplicate_charge_ref"]
    original_payment_id = candidate["payment_id"]

    reset1 = sc.reset_double_charge(core_eng, payment_eng)
    assert dup_payment_id in reset1.records["removed_payment_ids"]
    assert dup_charge_ref in reset1.records["removed_charge_refs"]

    with core_eng.connect() as conn:
        remaining_dup = conn.execute(
            text("SELECT count(*) FROM core.payments WHERE id = :id"), {"id": dup_payment_id}
        ).scalar_one()
        original_still_there = conn.execute(
            text("SELECT count(*) FROM core.payments WHERE id = :id"), {"id": original_payment_id}
        ).scalar_one()
    with payment_eng.connect() as conn:
        remaining_charge = conn.execute(
            text("SELECT count(*) FROM psp.charges WHERE charge_ref = :ref"), {"ref": dup_charge_ref}
        ).scalar_one()

    assert remaining_dup == 0
    assert original_still_there == 1
    assert remaining_charge == 0

    with core_eng.connect() as conn:
        assert sc.detect_double_charge(conn) == []

    reset2 = sc.reset_double_charge(core_eng, payment_eng)
    assert reset2.changes == []


def test_dry_run_changes_nothing(core_eng, payment_eng):
    with core_eng.connect() as conn:
        before_payments = conn.execute(text("SELECT count(*) FROM core.payments")).scalar_one()
    with payment_eng.connect() as conn:
        before_charges = conn.execute(text("SELECT count(*) FROM psp.charges")).scalar_one()

    result = runner.invoke(app, ["double_charge", "--dry-run", "--json"])
    assert result.exit_code == 0

    with core_eng.connect() as conn:
        after_payments = conn.execute(text("SELECT count(*) FROM core.payments")).scalar_one()
        detected = sc.detect_double_charge(conn)
    with payment_eng.connect() as conn:
        after_charges = conn.execute(text("SELECT count(*) FROM psp.charges")).scalar_one()

    assert before_payments == after_payments
    assert before_charges == after_charges
    assert detected == []
