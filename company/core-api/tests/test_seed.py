from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from conftest import DB_AVAILABLE, ensure_database

pytestmark = pytest.mark.skipif(not DB_AVAILABLE, reason="company-db is not reachable")


@pytest.fixture(scope="module")
def seeded_session():
    from app.bootstrap import bootstrap
    from app.seed import run_seed
    from app.settings import CoreApiSettings

    db_name = "netswift_core_test_seed"
    ensure_database(db_name)

    settings = CoreApiSettings(company_db_name=db_name, seed_on_startup=False)
    bootstrap(settings)

    engine = create_engine(settings.database_url)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    session = factory()
    run_seed(session, settings)
    yield session
    session.close()
    engine.dispose()


def test_seed_creates_7_packages(seeded_session):
    from app.models import Package

    count = seeded_session.scalar(select(func.count()).select_from(Package))
    assert count == 7


def test_seed_creates_200_customers(seeded_session):
    from app.models import Customer

    count = seeded_session.scalar(select(func.count()).select_from(Customer))
    assert count == 200


def test_seed_national_ids_are_all_unallocatable(seeded_session):
    from shared.fake_identity import national_id_is_valid

    from app.models import Customer

    national_ids = seeded_session.scalars(select(Customer.national_id)).all()
    assert len(national_ids) == 200
    for nid in national_ids:
        assert not national_id_is_valid(nid), f"{nid} unexpectedly looks like a real NI number"


def test_seed_status_distribution(seeded_session):
    from app.models import Subscription

    rows = seeded_session.execute(
        select(Subscription.status, func.count()).group_by(Subscription.status)
    ).all()
    counts = {status: count for status, count in rows}
    assert counts.get("active") == 150
    assert counts.get("provisioning") == 15
    assert counts.get("installation_scheduled") == 10
    assert counts.get("payment_received") == 10
    assert counts.get("awaiting_payment") == 8
    assert counts.get("suspended") == 5
    assert counts.get("cancelled") == 2


def test_seed_is_idempotent(seeded_session):
    from app.models import Customer
    from app.seed import run_seed
    from app.settings import CoreApiSettings

    ran_again = run_seed(seeded_session, CoreApiSettings(company_db_name="netswift_core_test_seed"))
    assert ran_again is False
    count = seeded_session.scalar(select(func.count()).select_from(Customer))
    assert count == 200


def test_seed_creates_service_accounts(seeded_session):
    from app.models import ServiceAccount

    names = set(seeded_session.scalars(select(ServiceAccount.name)).all())
    assert names == {"netswift-crm", "partner-integration"}
