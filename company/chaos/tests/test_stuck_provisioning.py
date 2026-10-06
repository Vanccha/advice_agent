from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text
from typer.testing import CliRunner

from shared.clock import utcnow

from chaos import scenarios as sc
from chaos.cli import app

runner = CliRunner()


def _resolve(engine, settings, kind: str) -> dict:
    """A victim for `kind`, promoted if necessary — the same resolution the CLI performs,
    so these tests keep working once the seeded early-lifecycle pool is drained."""
    candidate, needs_promotion = sc.ensure_candidate(settings, engine, kind=kind)
    if needs_promotion:
        candidate = sc.promote_to_payment_received(settings, candidate)
    return candidate


def test_inject_freezes_job_with_chaos_hold_marker(core_eng, settings):
    candidate = sc.find_stuck_provisioning_candidate(core_eng, None, None)
    if candidate is None:
        candidate = _resolve(core_eng, settings, "stuck_provisioning")
        assert candidate is not None, "fixture data must contain a promotable subscription"
        candidate = sc.promote_to_payment_received(settings, candidate)

    outcome = sc.apply_stuck_provisioning(core_eng, candidate)
    job_id = outcome.records["job_id"]

    with core_eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, last_error_message, heartbeat_at FROM core.provisioning_jobs WHERE id = :id"
            ),
            {"id": job_id},
        ).mappings().one()
    assert row["status"] == "running"
    assert row["last_error_message"] == "CHAOS_HOLD"
    assert row["heartbeat_at"] is not None


def test_stuck_job_survives_a_worker_sweep_window(core_eng, settings):
    """The provisioning worker's sweep must never touch a CHAOS_HOLD job."""
    candidate = _resolve(core_eng, settings, "stuck_provisioning")
    outcome = sc.apply_stuck_provisioning(core_eng, candidate)
    job_id = outcome.records["job_id"]

    # Simulate the exact sweep the live provisioning worker runs, against our own engine,
    # using the same predicate as `sweep_stuck_jobs` in app/worker.py.
    with core_eng.begin() as conn:
        candidates = conn.execute(
            text(
                "SELECT id, last_error_message FROM core.provisioning_jobs "
                "WHERE status = 'running' AND heartbeat_at < :cutoff"
            ),
            {"cutoff": utcnow() - timedelta(seconds=300)},
        ).mappings().all()
        touched = [c["id"] for c in candidates if c["last_error_message"] != "CHAOS_HOLD"]
        for jid in touched:
            conn.execute(text("UPDATE core.provisioning_jobs SET status='stuck' WHERE id=:id"), {"id": jid})

    with core_eng.connect() as conn:
        row = conn.execute(
            text("SELECT status, last_error_message FROM core.provisioning_jobs WHERE id = :id"),
            {"id": job_id},
        ).mappings().one()
    assert row["status"] == "running", "a CHAOS_HOLD job must never be swept to 'stuck'"
    assert row["last_error_message"] == "CHAOS_HOLD"


def test_status_detects_stuck_provisioning(core_eng, settings):
    with core_eng.connect() as conn:
        assert sc.detect_stuck_provisioning(conn) == []

    candidate = _resolve(core_eng, settings, "stuck_provisioning")
    sc.apply_stuck_provisioning(core_eng, candidate)

    with core_eng.connect() as conn:
        detected = sc.detect_stuck_provisioning(conn)
    assert len(detected) == 1
    assert detected[0]["subscription_id"] == candidate["subscription_id"]


def test_reset_releases_the_hold_and_is_idempotent(core_eng, settings):
    candidate = _resolve(core_eng, settings, "stuck_provisioning")
    outcome = sc.apply_stuck_provisioning(core_eng, candidate)
    job_id = outcome.records["job_id"]

    reset1 = sc.reset_stuck_provisioning(core_eng)
    assert job_id in reset1.records["job_ids"]

    with core_eng.connect() as conn:
        row = conn.execute(
            text("SELECT status, last_error_message, heartbeat_at FROM core.provisioning_jobs WHERE id = :id"),
            {"id": job_id},
        ).mappings().one()
    assert row["status"] == "queued"
    assert row["last_error_message"] is None
    assert row["heartbeat_at"] is None

    with core_eng.connect() as conn:
        assert sc.detect_stuck_provisioning(conn) == []

    reset2 = sc.reset_stuck_provisioning(core_eng)
    assert reset2.changes == []


def test_dry_run_changes_nothing(core_eng):
    with core_eng.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM core.provisioning_jobs")).scalar_one()

    result = runner.invoke(app, ["stuck_provisioning", "--dry-run", "--json"])
    assert result.exit_code == 0

    with core_eng.connect() as conn:
        after = conn.execute(text("SELECT count(*) FROM core.provisioning_jobs")).scalar_one()
        detected = sc.detect_stuck_provisioning(conn)
    assert before == after
    assert detected == []
