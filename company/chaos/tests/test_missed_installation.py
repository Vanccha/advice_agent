from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text
from typer.testing import CliRunner

from chaos import scenarios as sc
from chaos.cli import app

runner = CliRunner()


def test_inject_sets_yesterday_and_missed_subscription_unchanged(core_eng):
    candidate = sc.pick_missed_installation(core_eng, None, None)
    sub_id = candidate["subscription_id"]

    with core_eng.connect() as conn:
        sub_status_before = conn.execute(
            text("SELECT status FROM core.subscriptions WHERE id = :id"), {"id": sub_id}
        ).scalar_one()

    outcome = sc.apply_missed_installation(core_eng, candidate)
    appt_id = outcome.records["appointment_id"]

    with core_eng.connect() as conn:
        appt = conn.execute(
            text("SELECT scheduled_date, status FROM core.installation_appointments WHERE id = :id"),
            {"id": appt_id},
        ).mappings().one()
        sub_status_after = conn.execute(
            text("SELECT status FROM core.subscriptions WHERE id = :id"), {"id": sub_id}
        ).scalar_one()

    assert appt["status"] == "missed"
    assert appt["scheduled_date"] == date.today() - timedelta(days=1)
    assert sub_status_after == sub_status_before == "installation_scheduled"


def test_status_detects_missed_appointment(core_eng):
    with core_eng.connect() as conn:
        assert sc.detect_missed_installation(conn) == []

    candidate = sc.pick_missed_installation(core_eng, None, None)
    outcome = sc.apply_missed_installation(core_eng, candidate)

    with core_eng.connect() as conn:
        detected = sc.detect_missed_installation(conn)
    assert any(d["appointment_id"] == outcome.records["appointment_id"] for d in detected)


def test_reset_rebooks_and_is_idempotent(core_eng):
    candidate = sc.pick_missed_installation(core_eng, None, None)
    outcome = sc.apply_missed_installation(core_eng, candidate)
    appt_id = outcome.records["appointment_id"]

    reset1 = sc.reset_missed_installation(core_eng)
    assert appt_id in reset1.records["appointment_ids"]

    with core_eng.connect() as conn:
        appt = conn.execute(
            text("SELECT scheduled_date, status FROM core.installation_appointments WHERE id = :id"),
            {"id": appt_id},
        ).mappings().one()
    assert appt["status"] == "scheduled"
    assert appt["scheduled_date"] > date.today()

    with core_eng.connect() as conn:
        assert sc.detect_missed_installation(conn) == []

    reset2 = sc.reset_missed_installation(core_eng)
    assert reset2.changes == []


def test_dry_run_changes_nothing(core_eng):
    with core_eng.connect() as conn:
        before = conn.execute(
            text("SELECT count(*) FROM core.installation_appointments WHERE status = 'missed'")
        ).scalar_one()

    result = runner.invoke(app, ["missed_installation", "--dry-run", "--json"])
    assert result.exit_code == 0

    with core_eng.connect() as conn:
        after = conn.execute(
            text("SELECT count(*) FROM core.installation_appointments WHERE status = 'missed'")
        ).scalar_one()
    assert before == after == 0
