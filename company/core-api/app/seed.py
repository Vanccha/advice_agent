"""Deterministic, idempotent seed data for nethiz_core.

Run standalone with `python -m app.seed`, or via `app.bootstrap` on service startup
when SEED_ON_STARTUP=true. Safe to call repeatedly: skips if customers already exist.
"""
from __future__ import annotations

import logging
import random
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.auth import hash_api_key
from shared.clock import utcnow
from shared.fake_identity import (
    make_address,
    make_email,
    make_full_name,
    make_invalid_national_id,
    make_phone,
)

from app.models import (
    Customer,
    InstallationAppointment,
    Modem,
    NotificationLog,
    Package,
    Payment,
    ProvisioningJob,
    Region,
    ServiceAccount,
    Subscription,
    SubscriptionEvent,
)

logger = logging.getLogger(__name__)

SEED = 20261005

REGIONS = [
    ("IST-KAD", "Kadıköy", "İstanbul"),
    ("IST-BES", "Beşiktaş", "İstanbul"),
    ("IST-BAG", "Bağcılar", "İstanbul"),
    ("ANK-CAN", "Çankaya", "Ankara"),
    ("ANK-KEC", "Keçiören", "Ankara"),
    ("IZM-KAR", "Karşıyaka", "İzmir"),
    ("IZM-BOR", "Bornova", "İzmir"),
    ("BUR-NIL", "Nilüfer", "Bursa"),
    ("ANT-MUR", "Muratpaşa", "Antalya"),
    ("ADA-SEY", "Seyhan", "Adana"),
    ("KON-SEL", "Selçuklu", "Konya"),
    ("TRA-ORT", "Ortahisar", "Trabzon"),
]

PACKAGES = [
    dict(
        code="FIBER_50_OGRENCI", name="Öğrenci Fiber 50", down_mbps=50, up_mbps=10,
        commitment_months=12, monthly_price_try=269.00, setup_fee_try=0,
        target_profile="student", max_devices=8, static_ip=False, tv_included=False,
        gaming_optimized=False, description="Öğrenciler için uygun fiyatlı fiber paket.",
    ),
    dict(
        code="FIBER_100_TEMEL", name="Temel Fiber 100", down_mbps=100, up_mbps=20,
        commitment_months=24, monthly_price_try=349.00, setup_fee_try=0,
        target_profile="basic", max_devices=12, static_ip=False, tv_included=False,
        gaming_optimized=False, description="Günlük kullanım için temel fiber paket.",
    ),
    dict(
        code="FIBER_200_AILE", name="Aile Fiber 200", down_mbps=200, up_mbps=40,
        commitment_months=24, monthly_price_try=459.00, setup_fee_try=0,
        target_profile="family", max_devices=20, static_ip=False, tv_included=True,
        gaming_optimized=False, description="TV dahil aile paketi.",
    ),
    dict(
        code="FIBER_400_HOMEOFFICE", name="Home Office Fiber 400", down_mbps=400, up_mbps=80,
        commitment_months=24, monthly_price_try=629.00, setup_fee_try=199,
        target_profile="home_office", max_devices=30, static_ip=True, tv_included=False,
        gaming_optimized=False, description="Sabit IP'li ev ofisi paketi.",
    ),
    dict(
        code="FIBER_500_OYUNCU", name="Oyuncu Fiber 500", down_mbps=500, up_mbps=100,
        commitment_months=12, monthly_price_try=749.00, setup_fee_try=199,
        target_profile="gamer", max_devices=25, static_ip=False, tv_included=False,
        gaming_optimized=True, description="Oyuncular için düşük gecikmeli paket.",
    ),
    dict(
        code="FIBER_1000_PREMIUM", name="Premium Fiber 1000", down_mbps=1000, up_mbps=200,
        commitment_months=24, monthly_price_try=999.00, setup_fee_try=299,
        target_profile="premium", max_devices=50, static_ip=True, tv_included=True,
        gaming_optimized=True, description="Tüm özelliklerin dahil olduğu premium paket.",
    ),
    dict(
        code="FIBER_200_ESNEK", name="Esnek Fiber 200 (taahhütsüz)", down_mbps=200, up_mbps=40,
        commitment_months=0, monthly_price_try=589.00, setup_fee_try=299,
        target_profile="basic", max_devices=20, static_ip=False, tv_included=False,
        gaming_optimized=False, description="Taahhütsüz esnek fiber paket.",
    ),
]

# 200 customers: ~150 active, 15 provisioning, 10 installation_scheduled,
# 10 payment_received, 8 awaiting_payment, 5 suspended, 2 cancelled.
STATUS_DISTRIBUTION = (
    ["active"] * 150
    + ["provisioning"] * 15
    + ["installation_scheduled"] * 10
    + ["payment_received"] * 10
    + ["awaiting_payment"] * 8
    + ["suspended"] * 5
    + ["cancelled"] * 2
)

SERVICE_ACCOUNTS = [
    dict(
        name="nethiz-crm",
        env_key="core_api_key_crm",
        scopes=[
            "customers:*", "subscriptions:*", "payments:*", "billing:refund",
            "provisioning:*", "incidents:*", "appointments:*", "notifications:send",
            "credits:write",
        ],
    ),
    dict(
        name="partner-integration",
        env_key="core_api_key_partner",
        scopes=[
            "customers:read", "subscriptions:read", "payments:read", "provisioning:read",
            "provisioning:retry", "incidents:read", "appointments:read",
            "notifications:resend", "credits:write", "tickets:write",
        ],
    ),
]


def _seed_regions(session: Session) -> None:
    rng = random.Random(SEED)
    for code, name, city in REGIONS:
        if session.get(Region, code) is None:
            session.add(
                Region(code=code, name=name, city=city, olt_node_count=rng.randint(2, 6))
            )


def _seed_packages(session: Session) -> None:
    for pkg in PACKAGES:
        existing = session.scalar(select(Package).where(Package.code == pkg["code"]))
        if existing is None:
            session.add(Package(**pkg, is_active=True))


def _seed_service_accounts(session: Session, settings) -> None:
    for acc in SERVICE_ACCOUNTS:
        existing = session.scalar(select(ServiceAccount).where(ServiceAccount.name == acc["name"]))
        if existing is None:
            raw_key = getattr(settings, acc["env_key"])
            session.add(
                ServiceAccount(
                    name=acc["name"],
                    api_key_hash=hash_api_key(raw_key),
                    scopes=acc["scopes"],
                    is_active=True,
                )
            )


def _make_customer(rng: random.Random, idx: int, region_code: str) -> Customer:
    full_name = make_full_name(rng)
    return Customer(
        customer_no=f"NH-{100000 + idx}",
        full_name=full_name,
        national_id=make_invalid_national_id(rng),
        phone=make_phone(rng),
        email=make_email(full_name, rng),
        address_line=make_address(rng),
        district=region_code,
        city=region_code,
        region_code=region_code,
        kvkk_consent_at=utcnow(),
        created_at=utcnow(),
    )


def _seed_customers_and_subscriptions(session: Session) -> None:
    rng = random.Random(SEED)
    regions = session.scalars(select(Region)).all()
    packages = session.scalars(select(Package)).all()
    region_by_code = {r.code: r for r in regions}
    region_names = {code: (name, city) for code, name, city in REGIONS}

    statuses = list(STATUS_DISTRIBUTION)
    rng.shuffle(statuses)

    now = utcnow()

    for idx in range(1, 201):
        status = statuses[idx - 1]
        region_code = rng.choice(list(region_by_code.keys()))
        district, city = region_names[region_code]

        customer = _make_customer(rng, idx, region_code)
        customer.district = district
        customer.city = city
        session.add(customer)
        session.flush()

        package = rng.choice(packages)
        start_date = (now - timedelta(days=rng.randint(10, 400))).date()
        end_date = None
        if package.commitment_months:
            end_date = start_date + timedelta(days=30 * package.commitment_months)

        subscription = Subscription(
            customer_id=customer.id,
            package_id=package.id,
            status="registered",
            contract_start_date=start_date,
            contract_end_date=end_date,
            monthly_price_try=package.monthly_price_try,
            early_termination_fee_try=package.monthly_price_try * 2,
            created_at=now,
            updated_at=now,
        )
        session.add(subscription)
        session.flush()

        session.add(
            SubscriptionEvent(
                subscription_id=subscription.id,
                event_type="created",
                from_status=None,
                to_status="registered",
                actor="system",
                reason="seed",
                created_at=now,
            )
        )

        _advance_subscription(session, subscription, status, rng, now, customer, region_code)

    session.flush()


def _record_event(session, subscription, from_status, to_status, now, event_type="status_change"):
    session.add(
        SubscriptionEvent(
            subscription_id=subscription.id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            actor="system",
            reason="seed",
            created_at=now,
        )
    )
    subscription.status = to_status
    subscription.updated_at = now


def _advance_subscription(session, subscription, target_status, rng, now, customer, region_code):
    """Walk the subscription through the lifecycle up to target_status, with side records."""
    _record_event(session, subscription, "registered", "awaiting_payment", now)

    if target_status == "awaiting_payment":
        # Some stay unpaid, some have a failed attempt.
        if rng.random() < 0.5:
            session.add(
                Payment(
                    subscription_id=subscription.id,
                    customer_id=customer.id,
                    charge_ref=f"seed-fail-{subscription.id}",
                    amount_try=subscription.monthly_price_try,
                    status="failed",
                    method="card",
                    idempotency_key=f"seed-idem-{subscription.id}",
                    failure_code="CARD_DECLINED",
                    failure_message="Kart bankası tarafından reddedildi.",
                    created_at=now,
                    updated_at=now,
                )
            )
        return

    payment = Payment(
        subscription_id=subscription.id,
        customer_id=customer.id,
        charge_ref=f"seed-charge-{subscription.id}",
        amount_try=subscription.monthly_price_try,
        status="succeeded",
        method="card",
        idempotency_key=f"seed-idem-{subscription.id}",
        created_at=now,
        updated_at=now,
    )
    session.add(payment)
    _record_event(session, subscription, "awaiting_payment", "payment_received", now)

    if target_status == "payment_received":
        session.add(
            ProvisioningJob(
                subscription_id=subscription.id,
                status="queued",
                queued_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        return

    _record_event(session, subscription, "payment_received", "provisioning", now)
    job = ProvisioningJob(
        subscription_id=subscription.id,
        status="running" if target_status == "provisioning" else "succeeded",
        queued_at=now,
        started_at=now,
        finished_at=None if target_status == "provisioning" else now,
        heartbeat_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(job)

    if target_status == "provisioning":
        return

    modem = Modem(
        subscription_id=subscription.id,
        serial_no=f"SN-{subscription.id:08d}",
        mac_address=f"02:00:00:{subscription.id % 256:02x}:{(subscription.id // 256) % 256:02x}:00",
        model="ONT-X100",
        firmware="1.4.2",
        status="assigned",
        provisioned_at=now,
        created_at=now,
    )
    session.add(modem)
    _record_event(session, subscription, "provisioning", "provisioned", now)

    if target_status == "provisioned":
        return

    appointment_status = "scheduled"
    if target_status in ("installation_scheduled", "active", "suspended", "cancelled"):
        appointment_status = "scheduled"
    appointment = InstallationAppointment(
        subscription_id=subscription.id,
        scheduled_date=(now + timedelta(days=rng.randint(3, 10))).date(),
        time_slot=rng.choice(["09-12", "12-15", "15-18"]),
        team_code=f"FIELD-{region_code}-{rng.randint(1, 4)}",
        status=appointment_status,
        created_at=now,
        updated_at=now,
    )
    session.add(appointment)
    _record_event(session, subscription, "provisioned", "installation_scheduled", now)

    if target_status == "installation_scheduled":
        return

    # active, suspended, cancelled all pass through "active" first.
    modem.status = "online"
    appointment.status = "completed"
    _record_event(session, subscription, "installation_scheduled", "active", now)
    subscription.activated_at = now
    session.add(
        NotificationLog(
            customer_id=customer.id,
            subscription_id=subscription.id,
            channel=rng.choice(["sms", "email"]),
            template_code="ACTIVATION_READY",
            status="sent",
            sent_at=now,
            created_at=now,
        )
    )

    if target_status == "active":
        return

    if target_status == "suspended":
        _record_event(session, subscription, "active", "suspended", now)
        subscription.suspended_at = now
        return

    if target_status == "cancelled":
        _record_event(session, subscription, "active", "cancelled", now)
        subscription.cancellation_reason = "Müşteri talebiyle iptal edildi (seed)."
        return


def seed_reference_data(session: Session, settings) -> None:
    """Regions, packages and service accounts: always required for the API to function,
    independent of whether the 200 demo customers get seeded."""
    _seed_regions(session)
    _seed_packages(session)
    _seed_service_accounts(session, settings)
    session.commit()


def run_seed(session: Session, settings) -> bool:
    """Returns True if seeding actually ran, False if skipped (already seeded)."""
    seed_reference_data(session, settings)

    existing_customers = session.scalar(select(Customer.id).limit(1))
    if existing_customers is not None:
        logger.info("seed: customers already present, skipping customer/subscription seed")
        session.commit()
        return False

    _seed_customers_and_subscriptions(session)
    session.commit()
    logger.info("seed: created 200 customers with subscriptions")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from app.db import get_session_factory
    from app.settings import get_settings

    factory = get_session_factory()
    with factory() as db_session:
        run_seed(db_session, get_settings())
