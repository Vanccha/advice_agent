from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.auth import require_scope
from shared.errors import NotFound

from app.auth_registry import get_registry
from app.db import get_db
from app.models import Customer, IncidentSubscription, NetworkIncident, Subscription
from app.pagination import paginate
from app.serializers import incident_out

router = APIRouter(tags=["incidents"])


@router.get(
    "/v1/incidents",
    dependencies=[Depends(require_scope("incidents:read", get_registry()))],
)
def list_incidents(
    region_code: str | None = Query(default=None),
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(NetworkIncident)
    if region_code:
        stmt = stmt.where(NetworkIncident.region_code == region_code)
    if status:
        stmt = stmt.where(NetworkIncident.status == status)
    if severity:
        stmt = stmt.where(NetworkIncident.severity == severity)
    rows, total = paginate(db, stmt.order_by(NetworkIncident.id), offset=offset, limit=limit)
    return {"items": [incident_out(i) for i in rows], "total": total}


@router.get(
    "/v1/incidents/{incident_no}",
    dependencies=[Depends(require_scope("incidents:read", get_registry()))],
)
def get_incident(incident_no: str, db: Session = Depends(get_db)) -> dict:
    incident = db.scalar(select(NetworkIncident).where(NetworkIncident.incident_no == incident_no))
    if incident is None:
        raise NotFound("INCIDENT_NOT_FOUND", f"Incident '{incident_no}' not found.")
    customer_nos = db.scalars(
        select(Customer.customer_no)
        .join(Subscription, Subscription.customer_id == Customer.id)
        .join(
            IncidentSubscription,
            IncidentSubscription.subscription_id == Subscription.id,
        )
        .where(IncidentSubscription.incident_id == incident.id)
    ).all()
    payload = incident_out(incident)
    payload["affected_customer_no"] = list(customer_nos)
    return payload
