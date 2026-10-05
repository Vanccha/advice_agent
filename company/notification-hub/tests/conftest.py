from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# Point this test session at a dedicated test database, but keep connection params (user,
# password, host, port) from the environment so the test-runner container (COMPANY_DB_HOST=
# company-db) and a local run (COMPANY_DB_HOST=localhost) both work unchanged.
os.environ.setdefault("COMPANY_DB_USER", "nethiz")
os.environ.setdefault("COMPANY_DB_PASSWORD", "nethiz_dev_pw")
os.environ.setdefault("COMPANY_DB_HOST", "localhost")
os.environ.setdefault("COMPANY_DB_PORT", "55432")
os.environ.setdefault("NOTIFY_API_KEY", "nethiz_notify_key_change_me")
os.environ["COMPANY_DB_NAME"] = "nethiz_notify_test"

TEST_DB_NAME = "nethiz_notify_test"


def _database_available() -> bool:
    """Try to reach company-db and ensure the test database exists. False if unreachable."""
    user = os.environ["COMPANY_DB_USER"]
    password = os.environ["COMPANY_DB_PASSWORD"]
    host = os.environ["COMPANY_DB_HOST"]
    port = os.environ["COMPANY_DB_PORT"]
    admin_url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/postgres"
    try:
        engine = create_engine(admin_url, future=True, isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            conn.execute(text("COMMIT"))  # leave any implicit transaction before CREATE DATABASE
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": TEST_DB_NAME},
            ).scalar()
            if not exists:
                conn.execute(text(f"CREATE DATABASE {TEST_DB_NAME}"))
        engine.dispose()
        return True
    except SQLAlchemyError:
        return False
    except OSError:
        return False


_DB_AVAILABLE = _database_available()


@pytest.fixture(scope="session")
def app_client():
    """A TestClient against the real app, backed by nethiz_notify_test. Skips if DB is down."""
    if not _DB_AVAILABLE:
        pytest.skip(f"company-db is not reachable at {os.environ['COMPANY_DB_HOST']}; "
                    "skipping DB-backed notification-hub tests")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client(app_client):
    return app_client


@pytest.fixture(autouse=True)
def _clean_messages(request):
    """Truncate `messages` between DB-backed tests. No-op for tests that don't touch the DB."""
    if "client" not in request.fixturenames and "app_client" not in request.fixturenames:
        yield
        return
    if not _DB_AVAILABLE:
        yield
        return

    from app.db import SessionFactory
    from app.models import Message

    session = SessionFactory()
    try:
        session.query(Message).delete()
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture
def api_headers() -> dict[str, str]:
    return {"X-API-Key": os.environ["NOTIFY_API_KEY"]}
