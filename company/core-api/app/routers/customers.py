from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.auth import require_scope
from shared.clock import utcnow
from shared.errors import NotFound

from app.auth_registry import get_registry
from app.db import get_db
from app.models import Customer, Package, Subscription
from app.pagination import paginate
from app.schemas import CustomerCreate
from app.serializers import customer_out, subscription_out

router = APIRouter(tags=["customers"])


def _next_customer_no(db: Session) -> str:
    last = db.scalar(select(Customer).order_by(Customer.id.desc()))
    if last is None:
        return "NH-100001"
    last_seq = int(last.customer_no.split("-")[1])
    return f"NH-{last_seq + 1}"


@router.post(
    "/v1/customers",
    status_code=201,
    dependencies=[Depends(require_scope("customers:write", get_registry()))],
)
def create_customer(body: CustomerCreate, db: Session = Depends(get_db)) -> dict:
    customer = Customer(
        customer_no=_next_customer_no(db),
        full_name=body.full_name,
        national_id=body.national_id,
        phone=body.phone,
        email=body.email,
        address_line=body.address_line,
        district=body.district,
        city=body.city,
        region_code=body.region_code,
        kvkk_consent_at=utcnow() if body.kvkk_consent else None,
        created_at=utcnow(),
    )
    db.add(customer)
    db.flush()
    return customer_out(customer)


@router.get(
    "/v1/customers",
    dependencies=[Depends(require_scope("customers:read", get_registry()))],
)
def list_customers(
    customer_no: str | None = Query(default=None),
    phone: str | None = Query(default=None),
    email: str | None = Query(default=None),
    region_code: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(Customer)
    if customer_no:
        stmt = stmt.where(Customer.customer_no == customer_no)
    if phone:
        stmt = stmt.where(Customer.phone == phone)
    if email:
        stmt = stmt.where(Customer.email == email)
    if region_code:
        stmt = stmt.where(Customer.region_code == region_code)
    rows, total = paginate(db, stmt.order_by(Customer.id), offset=offset, limit=limit)
    return {"items": [customer_out(c) for c in rows], "total": total}


@router.get(
    "/v1/customers/{customer_no}",
    dependencies=[Depends(require_scope("customers:read", get_registry()))],
)
def get_customer(customer_no: str, db: Session = Depends(get_db)) -> dict:
    customer = db.scalar(select(Customer).where(Customer.customer_no == customer_no))
    if customer is None:
        raise NotFound("CUSTOMER_NOT_FOUND", f"Customer '{customer_no}' not found.")
    subs = db.scalars(
        select(Subscription).where(Subscription.customer_id == customer.id)
    ).all()
    summaries = []
    for sub in subs:
        package = db.get(Package, sub.package_id)
        summaries.append(
            {
                "id": sub.id,
                "status": sub.status,
                "package_code": package.code if package else None,
                "monthly_price_try": float(sub.monthly_price_try),
            }
        )
    payload = customer_out(customer)
    payload["subscriptions"] = summaries
    return payload
