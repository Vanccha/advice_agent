from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.auth import require_scope
from shared.clock import utcnow
from shared.errors import Conflict, NotFound

from app.auth_registry import get_registry
from app.db import get_db
from app.models import ProvisioningJob
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
