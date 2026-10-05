from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.auth import Principal, require_scope
from shared.clock import utcnow
from shared.errors import ApiError, NotFound

from app.auth_registry import get_registry
from app.db import get_db
from app.models import Credit, Payment, Refund
from app.payment_client import PaymentClient
from app.schemas import CreditRequest, RefundRequest
from app.settings import get_settings

router = APIRouter(tags=["billing"])


@router.post("/v1/refunds", status_code=201)
def create_refund(
    body: RefundRequest,
    principal: Principal = Depends(require_scope("billing:refund", get_registry())),
    db: Session = Depends(get_db),
) -> dict:
    payment = db.get(Payment, body.payment_id)
    if payment is None:
        raise NotFound("PAYMENT_NOT_FOUND", f"Payment {body.payment_id} not found.")
    if payment.status not in ("succeeded", "partially_refunded"):
        raise ApiError(
            "REFUND_NOT_ALLOWED",
            f"Payment {payment.id} is not refundable from status '{payment.status}'.",
            status_code=409,
        )

    now = utcnow()
    refund = Refund(
        payment_id=payment.id,
        amount_try=body.amount_try,
        status="requested",
        reason=body.reason,
        created_by=principal.name,
        created_at=now,
    )
    db.add(refund)
    db.flush()

    settings = get_settings()
    client = PaymentClient(settings)
    result = client.refund(
        charge_ref=payment.charge_ref, amount_try=body.amount_try, reason=body.reason
    )

    refund.refund_ref = result.get("refund_ref")
    refund.status = "completed" if result.get("status") == "succeeded" else "failed"

    if refund.status == "completed":
        if body.amount_try >= float(payment.amount_try):
            payment.status = "refunded"
        else:
            payment.status = "partially_refunded"
        payment.updated_at = now

    db.flush()
    return {
        "id": refund.id,
        "payment_id": refund.payment_id,
        "amount_try": float(refund.amount_try),
        "status": refund.status,
        "reason": refund.reason,
        "refund_ref": refund.refund_ref,
        "created_by": refund.created_by,
        "created_at": refund.created_at.isoformat() if refund.created_at else None,
    }


@router.post("/v1/credits", status_code=201)
def create_credit(
    body: CreditRequest,
    principal: Principal = Depends(require_scope("credits:write", get_registry())),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    if body.amount_try > settings.credit_max_per_request_try:
        raise ApiError(
            "CREDIT_LIMIT_EXCEEDED",
            f"Credit amount {body.amount_try} exceeds the per-request cap of "
            f"{settings.credit_max_per_request_try} TRY.",
            status_code=400,
        )

    existing = db.scalar(select(Credit).where(Credit.idempotency_key == body.idempotency_key))
    if existing is not None:
        return {
            "id": existing.id,
            "subscription_id": existing.subscription_id,
            "amount_try": float(existing.amount_try),
            "reason": existing.reason,
            "created_by": existing.created_by,
            "idempotency_key": existing.idempotency_key,
            "created_at": existing.created_at.isoformat() if existing.created_at else None,
        }

    credit = Credit(
        subscription_id=body.subscription_id,
        amount_try=body.amount_try,
        reason=body.reason,
        created_by=principal.name,
        idempotency_key=body.idempotency_key,
        created_at=utcnow(),
    )
    db.add(credit)
    db.flush()
    return {
        "id": credit.id,
        "subscription_id": credit.subscription_id,
        "amount_try": float(credit.amount_try),
        "reason": credit.reason,
        "created_by": credit.created_by,
        "idempotency_key": credit.idempotency_key,
        "created_at": credit.created_at.isoformat() if credit.created_at else None,
    }
