from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.auth import require_scope
from shared.clock import isoformat, utcnow
from shared.errors import ApiError, NotFound

from app.auth_registry import get_registry
from app.db import get_db
from app.models import Customer, NotificationLog
from app.schemas import NotificationResendRequest
from app.settings import get_settings

router = APIRouter(tags=["notifications"])


@router.post(
    "/v1/notifications/resend",
    status_code=201,
    dependencies=[Depends(require_scope("notifications:resend", get_registry()))],
)
def resend_notification(body: NotificationResendRequest, db: Session = Depends(get_db)) -> dict:
    customer = db.scalar(select(Customer).where(Customer.customer_no == body.customer_no))
    if customer is None:
        raise NotFound("CUSTOMER_NOT_FOUND", f"Customer '{body.customer_no}' not found.")

    settings = get_settings()
    cooldown = timedelta(seconds=settings.resend_cooldown_seconds)
    cutoff = utcnow() - cooldown

    recent = db.scalar(
        select(NotificationLog)
        .where(
            NotificationLog.customer_id == customer.id,
            NotificationLog.template_code == body.template_code,
            NotificationLog.created_at >= cutoff,
        )
        .order_by(NotificationLog.created_at.desc())
    )
    if recent is not None:
        raise ApiError(
            "RESEND_COOLDOWN",
            f"A '{body.template_code}' notification was already sent to "
            f"{body.customer_no} within the last {settings.resend_cooldown_seconds}s.",
            status_code=429,
        )

    now = utcnow()
    log = NotificationLog(
        customer_id=customer.id,
        channel=body.channel,
        template_code=body.template_code,
        status="sent",
        sent_at=now,
        created_at=now,
    )
    db.add(log)
    db.flush()
    return {
        "id": log.id,
        "customer_no": body.customer_no,
        "channel": log.channel,
        "template_code": log.template_code,
        "status": log.status,
        "sent_at": isoformat(log.sent_at),
    }
