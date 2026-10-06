from __future__ import annotations

import os

os.environ.setdefault("COMPANY_DB_HOST", "localhost")
os.environ.setdefault("COMPANY_DB_PORT", "5432")
os.environ.setdefault("COMPANY_DB_USER", "nethiz")
os.environ.setdefault("COMPANY_DB_PASSWORD", "nethiz_dev_pw")
os.environ["COMPANY_DB_NAME"] = "nethiz_core_test_worker"
os.environ.setdefault("PROVISION_SUCCESS_RATE", "0.9")
os.environ.setdefault("WORKER_INTERVAL_SECONDS", "5")
os.environ.setdefault("STUCK_AFTER_SECONDS", "300")

import pytest
from sqlalchemy import create_engine, text


def _admin_url(dbname: str = "postgres") -> str:
    from app.settings import get_settings

    s = get_settings()
    return (
        f"postgresql+psycopg://{s.company_db_user}:{s.company_db_password}"
        f"@{s.company_db_host}:{s.company_db_port}/{dbname}"
    )


def _database_reachable() -> bool:
    try:
        engine = create_engine(_admin_url())
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


DB_AVAILABLE = _database_reachable()


def _ensure_database(name: str) -> None:
    engine = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
        ).first()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine.dispose()


def _bootstrap_core_schema() -> None:
    """The worker never creates tables; core-api owns that. For an isolated test
    database we replicate the minimal subset of core-api's bootstrap: schema + tables
    for the models this worker touches."""
    from app.settings import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS core"))
    from shared.db import Base

    import app.models  # noqa: F401  (registers tables on Base.metadata)

    Base.metadata.create_all(engine)
    engine.dispose()


@pytest.fixture(scope="session")
def require_db():
    if not DB_AVAILABLE:
        pytest.skip("company-db is not reachable; skipping database-backed tests")


@pytest.fixture(scope="session", autouse=False)
def db_engine(require_db):
    _ensure_database("nethiz_core_test_worker")
    _bootstrap_core_schema()
    from app.db import get_engine

    yield get_engine()


# The helpers commit, so rows outlive a test and even a whole run. Sweep-style assertions
# count rows across the whole table, which made them depend on test order and on whatever
# previous runs had left behind (observed: a sweep expected to touch 1 job touched 5).
# Every test therefore starts from an empty core schema.
_TABLES_TO_CLEAR = (
    "core.provisioning_jobs",
    "core.modems",
    "core.installation_appointments",
    "core.subscription_events",
    "core.subscriptions",
    "core.customers",
)


@pytest.fixture(autouse=True)
def _clean_core_tables(db_engine):
    with db_engine.begin() as conn:
        for table in _TABLES_TO_CLEAR:
            conn.execute(text(f"DELETE FROM {table}"))
    yield


@pytest.fixture()
def session(db_engine):
    from app.db import get_session_factory

    factory = get_session_factory()
    sess = factory()
    yield sess
    sess.rollback()
    sess.close()
