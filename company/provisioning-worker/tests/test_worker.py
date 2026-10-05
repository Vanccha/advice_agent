from __future__ import annotations

import random
from datetime import timedelta

from shared.clock import utcnow

from app.models import InstallationAppointment, Modem, ProvisioningJob, Subscription
from app.settings import WorkerSettings
from app.worker import claim_job, run_job_to_completion, sweep_stuck_jobs
from helpers import make_customer, make_queued_job, make_running_job, make_subscription


def _settings(**overrides) -> WorkerSettings:
    base = dict(
        provision_success_rate=1.0,
        min_work_seconds=0.0,
        max_work_seconds=0.0,
        stuck_after_seconds=300,
    )
    base.update(overrides)
    return WorkerSettings(**base)


def _noop_sleep(_seconds: float) -> None:
    return None


def test_claim_job_moves_subscription_to_provisioning(session):
    customer = make_customer(session)
    sub = make_subscription(session, customer, status="payment_received")
    job = make_queued_job(session, sub)

    claimed = claim_job(session)
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"

    refreshed_sub = session.get(Subscription, sub.id)
    assert refreshed_sub.status == "provisioning"


def test_claim_job_returns_none_when_nothing_queued(session):
    assert claim_job(session) is None


def test_success_path_activates_modem_and_schedules_installation(session):
    customer = make_customer(session)
    sub = make_subscription(session, customer, status="payment_received")
    job = make_queued_job(session, sub)
    claimed = claim_job(session)

    settings = _settings(provision_success_rate=1.0)
    run_job_to_completion(session, claimed, settings, random.Random(1), sleeper=_noop_sleep)

    refreshed_job = session.get(ProvisioningJob, job.id)
    assert refreshed_job.status == "succeeded"
    assert refreshed_job.finished_at is not None

    refreshed_sub = session.get(Subscription, sub.id)
    assert refreshed_sub.status == "installation_scheduled"

    modem = session.query(Modem).filter_by(subscription_id=sub.id).one_or_none()
    assert modem is not None
    assert modem.status == "assigned"

    appointment = (
        session.query(InstallationAppointment).filter_by(subscription_id=sub.id).one_or_none()
    )
    assert appointment is not None
    assert appointment.team_code.startswith("FIELD-IST-KAD-")


def test_failure_path_sets_error_and_increments_attempt_count(session):
    customer = make_customer(session)
    sub = make_subscription(session, customer, status="payment_received")
    job = make_queued_job(session, sub)
    claimed = claim_job(session)

    settings = _settings(provision_success_rate=0.0)
    run_job_to_completion(session, claimed, settings, random.Random(2), sleeper=_noop_sleep)

    refreshed_job = session.get(ProvisioningJob, job.id)
    assert refreshed_job.status == "failed"
    assert refreshed_job.attempt_count == 1
    assert refreshed_job.last_error_code in ("OLT_PORT_BUSY", "VLAN_CONFLICT", "CPE_TIMEOUT")

    # Simulate an external retry (core-api's job): requeue and process again, this time
    # forcing success, to prove a failed job can be retried to completion.
    refreshed_job.status = "queued"
    refreshed_job.last_error_code = None
    refreshed_job.last_error_message = None
    session.commit()

    reclaimed = claim_job(session)
    assert reclaimed.id == job.id
    run_job_to_completion(session, reclaimed, _settings(provision_success_rate=1.0), random.Random(3), sleeper=_noop_sleep)
    final_job = session.get(ProvisioningJob, job.id)
    assert final_job.status == "succeeded"
    assert final_job.attempt_count == 1  # only the failure path increments attempt_count


def test_sweep_marks_stale_running_job_as_stuck(session):
    customer = make_customer(session)
    sub = make_subscription(session, customer, status="provisioning")
    stale_heartbeat = utcnow() - timedelta(seconds=600)
    job = make_running_job(session, sub, heartbeat_at=stale_heartbeat)

    settings = _settings(stuck_after_seconds=300)
    touched = sweep_stuck_jobs(session, settings)

    assert touched == 1
    refreshed = session.get(ProvisioningJob, job.id)
    assert refreshed.status == "stuck"


def test_sweep_never_touches_chaos_hold_job(session):
    customer = make_customer(session)
    sub = make_subscription(session, customer, status="provisioning")
    stale_heartbeat = utcnow() - timedelta(seconds=1800)
    job = make_running_job(
        session, sub, heartbeat_at=stale_heartbeat, last_error_message="CHAOS_HOLD"
    )

    settings = _settings(stuck_after_seconds=300)
    touched = sweep_stuck_jobs(session, settings)

    assert touched == 0
    refreshed = session.get(ProvisioningJob, job.id)
    assert refreshed.status == "running"
    assert refreshed.last_error_message == "CHAOS_HOLD"


def test_sweep_leaves_fresh_running_jobs_alone(session):
    customer = make_customer(session)
    sub = make_subscription(session, customer, status="provisioning")
    job = make_running_job(session, sub, heartbeat_at=utcnow())

    settings = _settings(stuck_after_seconds=300)
    touched = sweep_stuck_jobs(session, settings)

    assert touched == 0
    refreshed = session.get(ProvisioningJob, job.id)
    assert refreshed.status == "running"
