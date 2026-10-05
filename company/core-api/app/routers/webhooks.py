from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.clock import utcnow
from shared.errors import Unauthorized

from app.db import get_db
from app.lifecycle import transition
from app.models import Payment, ProvisioningJob, Subscription
from app.serializers import payment_out
from app.settings import get_settings

router = APIRouter(tags=["webhooks"])


class PaymentWebhookBody(BaseModel):
    charge_ref: str
    status: str
    failure_code: str | None = None
    failure_message: str | None = None


def _verify_secret(x_webhook_secret: str | None) -> None:
    settings = get_settings()
    if not x_webhook_secret or x_webhook_secret != settings.webhook_secret:
        raise Unauthorized("INVALID_WEBHOOK_SECRET", "X-Webhook-Secret header is missing or wrong.")


@router.post("/v1/webhooks/payment")
def payment_webhook(
    body: PaymentWebhookBody,
    db: Session = Depends(get_db),
    x_webhook_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    _verify_secret(x_webhook_secret)

    payment = db.scalar(select(Payment).where(Payment.charge_ref == body.charge_ref))
    if payment is None:
        return {"status": "ignored", "reason": "unknown charge_ref"}

    now = utcnow()
    payment.status = body.status
    payment.failure_code = body.failure_code
    payment.failure_message = body.failure_message
    payment.updated_at = now

    sub = db.get(Subscription, payment.subscription_id)
    if sub is not None and body.status == "succeeded" and sub.status in (
        "registered",
        "awaiting_payment",
    ):
        transition(db, sub, "payment_received", actor="system", reason="webhook: payment succeeded")
        db.add(
            ProvisioningJob(
                subscription_id=sub.id,
                status="queued",
                queued_at=now,
                created_at=now,
                updated_at=now,
            )
        )

    db.flush()
    return {"status": "ok", "payment": payment_out(payment)}
