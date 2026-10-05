from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.auth import require_scope
from shared.clock import utcnow
from shared.errors import Conflict, NotFound

from app.auth_registry import get_registry
from app.db import get_db
from app.models import ProvisioningJob, Subscription
from app.pagination import paginate
from app.serializers import job_out

router = APIRouter(tags=["provisioning"])


@router.get(
    "/v1/provisioning-jobs",
    dependencies=[Depends(require_scope("provisioning:read", get_registry()))],
)
def list_provisioning_jobs(
    subscription_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(ProvisioningJob)
    if subscription_id is not None:
        stmt = stmt.where(ProvisioningJob.subscription_id == subscription_id)
    if status:
        stmt = stmt.where(ProvisioningJob.status == status)
    rows, total = paginate(db, stmt.order_by(ProvisioningJob.id), offset=offset, limit=limit)
    return {"items": [job_out(j) for j in rows], "total": total}


@router.post(
    "/v1/provisioning-jobs/{job_id}/retry",
    dependencies=[Depends(require_scope("provisioning:retry", get_registry()))],
)
def retry_provisioning_job(job_id: int, db: Session = Depends(get_db)) -> dict:
    job = db.get(ProvisioningJob, job_id)
    if job is None:
        raise NotFound("PROVISIONING_JOB_NOT_FOUND", f"Provisioning job {job_id} not found.")
    if job.status not in ("stuck", "failed"):
        raise Conflict(
            "ILLEGAL_TRANSITION",
            f"Provisioning job {job_id} cannot be retried from status '{job.status}'.",
        )
    job.status = "queued"
    job.attempt_count = (job.attempt_count or 0) + 1
    job.last_error_code = None
    job.last_error_message = None
    job.queued_at = utcnow()
    job.started_at = None
    job.finished_at = None
    job.heartbeat_at = None
    job.updated_at = utcnow()
    db.flush()
    return job_out(job)


# A subscription whose payment succeeded but whose provisioning was never queued (a gap the
# payment webhook can leave behind) needs a way back into the pipeline without a CSR opening
# the database. This is the company's own repair endpoint; any API client with the scope may
# call it.
ENQUEUEABLE_SUBSCRIPTION_STATUSES = ("payment_received", "provisioning")
ACTIVE_JOB_STATUSES = ("queued", "running", "succeeded")


@router.post(
    "/v1/subscriptions/{subscription_id}/provisioning-jobs",
    dependencies=[Depends(require_scope("provisioning:retry", get_registry()))],
    status_code=201,
)
def enqueue_provisioning_job(subscription_id: int, db: Session = Depends(get_db)) -> dict:
    subscription = db.get(Subscription, subscription_id)
    if subscription is None:
        raise NotFound("SUBSCRIPTION_NOT_FOUND", f"Subscription {subscription_id} not found.")
    if subscription.status not in ENQUEUEABLE_SUBSCRIPTION_STATUSES:
        raise Conflict(
            "ILLEGAL_TRANSITION",
            "Provisioning can only be queued for a subscription whose payment has been "
            f"received; subscription {subscription_id} is '{subscription.status}'.",
        )
    existing = db.scalars(
        select(ProvisioningJob)
        .where(ProvisioningJob.subscription_id == subscription_id)
        .where(ProvisioningJob.status.in_(ACTIVE_JOB_STATUSES))
    ).first()
    if existing is not None:
        raise Conflict(
            "PROVISIONING_JOB_ALREADY_ACTIVE",
            f"Subscription {subscription_id} already has a provisioning job "
            f"({existing.id}) in status '{existing.status}'.",
            job_id=existing.id,
            job_status=existing.status,
        )
    job = ProvisioningJob(
        subscription_id=subscription_id,
        status="queued",
        attempt_count=0,
        queued_at=utcnow(),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(job)
    db.flush()
    return job_out(job)
