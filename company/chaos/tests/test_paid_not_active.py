from __future__ import annotations

from sqlalchemy import text
from typer.testing import CliRunner

from chaos import scenarios as sc
from chaos.cli import app

runner = CliRunner()


def _get_candidate(core_eng, settings):
    candidate = sc.find_paid_not_active_candidate(core_eng, None, None)
    if candidate is None:
        promo = sc.find_promotable_subscription(core_eng, None, None)
        assert promo is not None, "fixture data must contain a promotable subscription"
        candidate = sc.promote_to_payment_received(settings, promo)
    return candidate


def test_inject_leaves_payment_received_with_no_job(core_eng, settings):
    candidate = _get_candidate(core_eng, settings)
    sub_id = candidate["subscription_id"]

    sc.apply_paid_not_active(core_eng, candidate)

    with core_eng.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM core.subscriptions WHERE id = :id"), {"id": sub_id}
        ).scalar_one()
        job_count = conn.execute(
            text(
                "SELECT count(*) FROM core.provisioning_jobs WHERE subscription_id = :id "
                "AND status IN ('queued', 'running')"
            ),
            {"id": sub_id},
        ).scalar_one()
        payment_status = conn.execute(
            text(
                "SELECT status FROM core.payments WHERE subscription_id = :id "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"id": sub_id},
        ).scalar_one()

    assert status == "payment_received"
    assert job_count == 0
    assert payment_status == "succeeded"


def test_status_detects_paid_not_active(core_eng, settings):
    with core_eng.connect() as conn:
        assert sc.detect_paid_not_active(conn) == []

    candidate = _get_candidate(core_eng, settings)
    sc.apply_paid_not_active(core_eng, candidate)

    with core_eng.connect() as conn:
        detected = sc.detect_paid_not_active(conn)
    assert any(d["subscription_id"] == candidate["subscription_id"] for d in detected)


def test_reset_requeues_a_fresh_job_and_is_idempotent(core_eng, settings):
    candidate = _get_candidate(core_eng, settings)
    sub_id = candidate["subscription_id"]
    sc.apply_paid_not_active(core_eng, candidate)

    reset1 = sc.reset_paid_not_active(core_eng)
    assert reset1.changes, "reset should have created a replacement job"

    with core_eng.connect() as conn:
        job = conn.execute(
            text(
                "SELECT status FROM core.provisioning_jobs WHERE subscription_id = :id "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"id": sub_id},
        ).mappings().first()
    assert job is not None
    assert job["status"] == "queued"

    with core_eng.connect() as conn:
        assert all(d["subscription_id"] != sub_id for d in sc.detect_paid_not_active(conn))

    reset2 = sc.reset_paid_not_active(core_eng)
    assert all("subscription " + str(sub_id) not in c for c in reset2.changes)


def test_dry_run_changes_nothing(core_eng):
    with core_eng.connect() as conn:
        before_jobs = conn.execute(text("SELECT count(*) FROM core.provisioning_jobs")).scalar_one()
        before_payments = conn.execute(text("SELECT count(*) FROM core.payments")).scalar_one()

    result = runner.invoke(app, ["paid_not_active", "--dry-run", "--json"])
    assert result.exit_code == 0

    with core_eng.connect() as conn:
        after_jobs = conn.execute(text("SELECT count(*) FROM core.provisioning_jobs")).scalar_one()
        after_payments = conn.execute(text("SELECT count(*) FROM core.payments")).scalar_one()
        detected = sc.detect_paid_not_active(conn)

    assert before_jobs == after_jobs
    assert before_payments == after_payments
    assert detected == []
