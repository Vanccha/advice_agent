from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.auth import require_scope
from shared.clock import utcnow
from shared.errors import NotFound

from app.auth_registry import get_registry
from app.db import get_db
from app.models import InstallationAppointment
from app.pagination import paginate
from app.schemas import RescheduleRequest
from app.serializers import appointment_out

router = APIRouter(tags=["appointments"])


@router.get(
    "/v1/installation-appointments",
    dependencies=[Depends(require_scope("appointments:read", get_registry()))],
)
def list_appointments(
    subscription_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    team_code: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(InstallationAppointment)
    if subscription_id is not None:
        stmt = stmt.where(InstallationAppointment.subscription_id == subscription_id)
    if status:
        stmt = stmt.where(InstallationAppointment.status == status)
    if team_code:
        stmt = stmt.where(InstallationAppointment.team_code == team_code)
    rows, total = paginate(
        db, stmt.order_by(InstallationAppointment.id), offset=offset, limit=limit
    )
    return {"items": [appointment_out(a) for a in rows], "total": total}


@router.post(
    "/v1/installation-appointments/{appointment_id}/reschedule",
    dependencies=[Depends(require_scope("appointments:write", get_registry()))],
)
def reschedule_appointment(
    appointment_id: int, body: RescheduleRequest, db: Session = Depends(get_db)
) -> dict:
    appt = db.get(InstallationAppointment, appointment_id)
    if appt is None:
        raise NotFound("APPOINTMENT_NOT_FOUND", f"Appointment {appointment_id} not found.")
    appt.scheduled_date = body.scheduled_date
    appt.time_slot = body.time_slot
    appt.status = "rescheduled"
    appt.updated_at = utcnow()
    db.flush()
    return appointment_out(appt)
