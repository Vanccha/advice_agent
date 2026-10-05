from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.auth import require_scope
from shared.clock import utcnow
from shared.errors import ApiError, Conflict, NotFound

from app.auth_registry import get_registry
from app.db import get_db
from app.lifecycle import assert_transition, transition
from app.models import (
    Customer,
    InstallationAppointment,
    Package,
    Payment,
    ProvisioningJob,
    Subscription,
)
from app.pagination import paginate
from app.payment_client import PaymentClient
from app.schemas import CancelRequest, PaymentCreate, SubscriptionCreate, TransitionRequest
from app.serializers import appointment_out, job_out, payment_out, subscription_out
from app.settings import get_settings

router = APIRouter(tags=["subscriptions"])


def _get_subscription_or_404(db: Session, subscription_id: int) -> Subscription:
    sub = db.get(Subscription, subscription_id)
    if sub is None:
        raise NotFound("SUBSCRIPTION_NOT_FOUND", f"Subscription {subscription_id} not found.")
    return sub


@router.post(
    "/v1/subscriptions",
    status_code=201,
    dependencies=[Depends(require_scope("subscriptions:write", get_registry()))],
)
def create_subscription(body: SubscriptionCreate, db: Session = Depends(get_db)) -> dict:
    customer = db.scalar(select(Customer).where(Customer.customer_no == body.customer_no))
    if customer is None:
        raise NotFound("CUSTOMER_NOT_FOUND", f"Customer '{body.customer_no}' not found.")
    package = db.scalar(select(Package).where(Package.code == body.package_code))
    if package is None:
        raise NotFound("PACKAGE_NOT_FOUND", f"Package '{body.package_code}' not found.")

    now = utcnow()
    sub = Subscription(
        customer_id=customer.id,
        package_id=package.id,
        status="registered",
        monthly_price_try=package.monthly_price_try,
        early_termination_fee_try=package.monthly_price_try * 2,
        created_at=now,
        updated_at=now,
    )
    db.add(sub)
    db.flush()
    from app.models import SubscriptionEvent

    db.add(
        SubscriptionEvent(
            subscription_id=sub.id,
            event_type="created",
            from_status=None,
            to_status="registered",
            actor="api_client",
            created_at=now,
        )
    )
    return subscription_out(sub)


@router.get(
    "/v1/subscriptions",
    dependencies=[Depends(require_scope("subscriptions:read", get_registry()))],
)
def list_subscriptions(
    customer_no: str | None = Query(default=None),
    status: str | None = Query(default=None),
    region_code: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(Subscription).join(Customer, Customer.id == Subscription.customer_id)
    if customer_no:
        stmt = stmt.where(Customer.customer_no == customer_no)
    if status:
        stmt = stmt.where(Subscription.status == status)
    if region_code:
        stmt = stmt.where(Customer.region_code == region_code)
    rows, total = paginate(db, stmt.order_by(Subscription.id), offset=offset, limit=limit)
    return {"items": [subscription_out(s) for s in rows], "total": total}


@router.get(
    "/v1/subscriptions/{subscription_id}",
    dependencies=[Depends(require_scope("subscriptions:read", get_registry()))],
)
def get_subscription(subscription_id: int, db: Session = Depends(get_db)) -> dict:
    sub = _get_subscription_or_404(db, subscription_id)
    payload = subscription_out(sub)

    latest_payment = db.scalar(
        select(Payment)
        .where(Payment.subscription_id == sub.id)
        .order_by(Payment.id.desc())
    )
    latest_job = db.scalar(
        select(ProvisioningJob)
        .where(ProvisioningJob.subscription_id == sub.id)
        .order_by(ProvisioningJob.id.desc())
    )
    latest_appointment = db.scalar(
        select(InstallationAppointment)
        .where(InstallationAppointment.subscription_id == sub.id)
        .order_by(InstallationAppointment.id.desc())
    )
    payload["latest_payment"] = payment_out(latest_payment) if latest_payment else None
    payload["latest_provisioning_job"] = job_out(latest_job) if latest_job else None
    payload["latest_appointment"] = appointment_out(latest_appointment) if latest_appointment else None
    return payload


@router.post(
    "/v1/subscriptions/{subscription_id}/payments",
    status_code=201,
    dependencies=[Depends(require_scope("payments:write", get_registry()))],
)
def create_payment(
    subscription_id: int, body: PaymentCreate, db: Session = Depends(get_db)
) -> dict:
    sub = _get_subscription_or_404(db, subscription_id)

    existing = db.scalar(
        select(Payment).where(Payment.idempotency_key == body.idempotency_key)
    )
    if existing is not None:
        return payment_out(existing)

    if sub.status not in ("registered", "awaiting_payment"):
        raise Conflict(
            "ILLEGAL_TRANSITION",
            f"Subscription {sub.id} cannot accept a payment from status '{sub.status}'.",
        )

    customer = db.get(Customer, sub.customer_id)
    amount = body.amount_try if body.amount_try is not None else float(sub.monthly_price_try)

    if sub.status == "registered":
        transition(db, sub, "awaiting_payment", actor="api_client", reason="payment initiated")
    now = utcnow()

    payment = Payment(
        subscription_id=sub.id,
        customer_id=sub.customer_id,
        amount_try=amount,
        status="pending",
        method=body.method,
        idempotency_key=body.idempotency_key,
        created_at=now,
        updated_at=now,
    )
    db.add(payment)
    db.flush()

    settings = get_settings()
    client = PaymentClient(settings)
    try:
        result = client.create_charge(
            amount_try=amount,
            customer_ref=customer.customer_no if customer else str(sub.customer_id),
            method=body.method,
            card_token=body.card_token,
            idempotency_key=body.idempotency_key,
        )
    except ApiError:
        # PSP unreachable / outage: commit so the payment row survives as 'pending',
        # then surface the 503 to the caller.
        db.commit()
        raise

    payment.charge_ref = result.get("charge_ref")
    payment.gateway_response = result
    payment.updated_at = utcnow()

    if result.get("status") == "succeeded":
        payment.status = "succeeded"
        transition(db, sub, "payment_received", actor="system", reason="payment succeeded")
        db.add(
            ProvisioningJob(
                subscription_id=sub.id,
                status="queued",
                queued_at=utcnow(),
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
    else:
        payment.status = "failed"
        payment.failure_code = result.get("failure_code")
        payment.failure_message = result.get("failure_message", "Payment failed.")

    db.flush()
    return payment_out(payment)


@router.get(
    "/v1/subscriptions/{subscription_id}/payments",
    dependencies=[Depends(require_scope("payments:read", get_registry()))],
)
def list_subscription_payments(subscription_id: int, db: Session = Depends(get_db)) -> dict:
    _get_subscription_or_404(db, subscription_id)
    rows = db.scalars(
        select(Payment)
        .where(Payment.subscription_id == subscription_id)
        .order_by(Payment.id)
    ).all()
    return {"items": [payment_out(p) for p in rows], "total": len(rows)}


@router.post(
    "/v1/subscriptions/{subscription_id}/transitions",
    dependencies=[Depends(require_scope("subscriptions:write", get_registry()))],
)
def apply_transition(
    subscription_id: int, body: TransitionRequest, db: Session = Depends(get_db)
) -> dict:
    sub = _get_subscription_or_404(db, subscription_id)
    assert_transition(sub.status, body.to_status)
    transition(db, sub, body.to_status, actor="csr", reason=body.reason)
    db.flush()
    return subscription_out(sub)


@router.post(
    "/v1/subscriptions/{subscription_id}/cancel",
    dependencies=[Depends(require_scope("subscriptions:write", get_registry()))],
)
def cancel_subscription(
    subscription_id: int, body: CancelRequest, db: Session = Depends(get_db)
) -> dict:
    sub = _get_subscription_or_404(db, subscription_id)
    transition(db, sub, "cancelled", actor="csr", reason=body.reason)
    sub.cancellation_reason = body.reason
    db.flush()
    return subscription_out(sub)
