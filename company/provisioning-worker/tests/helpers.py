from __future__ import annotations

import itertools

from app.models import Customer, ProvisioningJob, Subscription
from shared.clock import utcnow

_counter = itertools.count(1)


def make_customer(session) -> Customer:
    n = next(_counter)
    customer = Customer(customer_no=f"NH-{900000 + n}", region_code="IST-KAD")
    session.add(customer)
    session.flush()
    return customer


def make_subscription(session, customer: Customer, status: str = "payment_received") -> Subscription:
    sub = Subscription(customer_id=customer.id, status=status, updated_at=utcnow())
    session.add(sub)
    session.flush()
    return sub


def make_queued_job(session, subscription: Subscription) -> ProvisioningJob:
    job = ProvisioningJob(
        subscription_id=subscription.id,
        status="queued",
        queued_at=utcnow(),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(job)
    session.flush()
    session.commit()
    return job


def make_running_job(session, subscription: Subscription, *, heartbeat_at, last_error_message=None) -> ProvisioningJob:
    now = utcnow()
    job = ProvisioningJob(
        subscription_id=subscription.id,
        status="running",
        queued_at=now,
        started_at=now,
        heartbeat_at=heartbeat_at,
        last_error_message=last_error_message,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.flush()
    session.commit()
    return job
