from __future__ import annotations

import logging
import random
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.clock import utcnow

from app.metrics import (
    PROVISIONING_JOB_DURATION_SECONDS,
    PROVISIONING_JOBS_STUCK,
    PROVISIONING_JOBS_TOTAL,
    WORKER_SWEEPS_TOTAL,
)
from app.models import (
    Customer,
    InstallationAppointment,
    Modem,
    ProvisioningJob,
    Subscription,
    SubscriptionEvent,
)
from app.settings import WorkerSettings

logger = logging.getLogger(__name__)

ERROR_CODES = ("OLT_PORT_BUSY", "VLAN_CONFLICT", "CPE_TIMEOUT")
ERROR_MESSAGES = {
    "OLT_PORT_BUSY": "OLT port is currently busy.",
    "VLAN_CONFLICT": "VLAN id conflict detected on this OLT node.",
    "CPE_TIMEOUT": "Customer premises equipment did not respond in time.",
}


def _record_subscription_event(session: Session, subscription: Subscription, to_status: str, reason: str) -> None:
    from_status = subscription.status
    subscription.status = to_status
    subscription.updated_at = utcnow()
    if to_status == "active" and subscription.activated_at is None:
        subscription.activated_at = utcnow()
    session.add(
        SubscriptionEvent(
            subscription_id=subscription.id,
            event_type="status_change",
            from_status=from_status,
            to_status=to_status,
            actor="job",
            reason=reason,
            created_at=utcnow(),
        )
    )


def claim_job(session: Session) -> ProvisioningJob | None:
    """Claim exactly one queued job with SELECT ... FOR UPDATE SKIP LOCKED."""
    job = session.execute(
        select(ProvisioningJob)
        .where(ProvisioningJob.status == "queued")
        .order_by(ProvisioningJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if job is None:
        session.rollback()
        return None

    now = utcnow()
    job.status = "running"
    job.started_at = now
    job.heartbeat_at = now
    job.updated_at = now

    sub = session.get(Subscription, job.subscription_id)
    if sub is not None and sub.status == "payment_received":
        _record_subscription_event(session, sub, "provisioning", "provisioning job started")

    session.commit()
    PROVISIONING_JOBS_TOTAL.labels(status="running").inc()
    return job


def heartbeat(session: Session, job: ProvisioningJob) -> None:
    job.heartbeat_at = utcnow()
    job.updated_at = utcnow()
    session.add(job)
    session.commit()


def _assign_modem(session: Session, subscription_id: int) -> Modem:
    modem = session.scalar(
        select(Modem).where(Modem.status == "in_stock").order_by(Modem.id).limit(1)
    )
    now = utcnow()
    if modem is None:
        suffix = f"{subscription_id:08d}-{int(now.timestamp() * 1000) % 10_000_000}"
        modem = Modem(
            serial_no=f"SN-{suffix}",
            mac_address=f"02:AA:BB:{subscription_id % 256:02X}:{(subscription_id // 256) % 256:02X}:01",
            model="ONT-X100",
            firmware="1.4.2",
            status="in_stock",
            created_at=now,
        )
        session.add(modem)
        session.flush()
    modem.subscription_id = subscription_id
    modem.status = "assigned"
    modem.provisioned_at = now
    return modem


def complete_job_success(session: Session, job: ProvisioningJob, rng: random.Random) -> None:
    now = utcnow()
    job.status = "succeeded"
    job.finished_at = now
    job.heartbeat_at = now
    job.updated_at = now
    job.olt_node = job.olt_node or f"OLT-{rng.randint(1, 20):03d}"
    job.vlan_id = job.vlan_id or rng.randint(100, 999)

    sub = session.get(Subscription, job.subscription_id)
    _assign_modem(session, job.subscription_id)

    if sub is not None:
        _record_subscription_event(session, sub, "provisioned", "provisioning job succeeded")

        customer = session.get(Customer, sub.customer_id)
        region_code = customer.region_code if customer else "GEN"
        appointment = InstallationAppointment(
            subscription_id=sub.id,
            scheduled_date=(now + timedelta(days=rng.randint(3, 10))).date(),
            time_slot=rng.choice(["09-12", "12-15", "15-18"]),
            team_code=f"FIELD-{region_code}-{rng.randint(1, 4)}",
            status="scheduled",
            created_at=now,
            updated_at=now,
        )
        session.add(appointment)

        _record_subscription_event(
            session, sub, "installation_scheduled", "installation appointment created"
        )

    session.commit()
    PROVISIONING_JOBS_TOTAL.labels(status="succeeded").inc()


def complete_job_failure(session: Session, job: ProvisioningJob, rng: random.Random) -> None:
    now = utcnow()
    error_code = rng.choice(ERROR_CODES)
    job.status = "failed"
    job.finished_at = now
    job.heartbeat_at = now
    job.updated_at = now
    job.attempt_count = (job.attempt_count or 0) + 1
    job.last_error_code = error_code
    job.last_error_message = ERROR_MESSAGES[error_code]
    session.commit()
    PROVISIONING_JOBS_TOTAL.labels(status="failed").inc()


def run_job_to_completion(
    session: Session,
    job: ProvisioningJob,
    settings: WorkerSettings,
    rng: random.Random,
    sleeper=time.sleep,
) -> None:
    """Simulate 2-6s of provisioning work, heartbeating roughly every second."""
    work_seconds = rng.uniform(settings.min_work_seconds, settings.max_work_seconds)
    started = time.monotonic()
    elapsed = 0.0
    while elapsed < work_seconds:
        step = min(1.0, work_seconds - elapsed)
        sleeper(step)
        elapsed += step
        heartbeat(session, job)

    duration = time.monotonic() - started
    PROVISIONING_JOB_DURATION_SECONDS.observe(duration)

    if rng.random() < settings.provision_success_rate:
        complete_job_success(session, job, rng)
    else:
        complete_job_failure(session, job, rng)


def sweep_stuck_jobs(session: Session, settings: WorkerSettings) -> int:
    """Mark stale `running` jobs as `stuck`. Never touches a CHAOS_HOLD job."""
    cutoff = utcnow() - timedelta(seconds=settings.stuck_after_seconds)
    candidates = session.scalars(
        select(ProvisioningJob).where(
            ProvisioningJob.status == "running",
            ProvisioningJob.heartbeat_at < cutoff,
        )
    ).all()
    touched = 0
    for job in candidates:
        if job.last_error_message == "CHAOS_HOLD":
            continue  # chaos scenario a: intentionally frozen, must never be swept
        job.status = "stuck"
        job.updated_at = utcnow()
        touched += 1
    if touched:
        session.commit()
        PROVISIONING_JOBS_TOTAL.labels(status="stuck").inc(touched)
    else:
        session.rollback()

    WORKER_SWEEPS_TOTAL.inc()
    stuck_total = len(
        session.scalars(select(ProvisioningJob).where(ProvisioningJob.status == "stuck")).all()
    )
    PROVISIONING_JOBS_STUCK.set(stuck_total)
    return touched


def run_once(
    session: Session, settings: WorkerSettings, rng: random.Random, sleeper=time.sleep
) -> bool:
    """One full tick: sweep stuck jobs, then claim and process at most one job."""
    sweep_stuck_jobs(session, settings)
    job = claim_job(session)
    if job is None:
        return False
    run_job_to_completion(session, job, settings, rng, sleeper=sleeper)
    return True
