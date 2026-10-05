from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from shared.db import make_engine, wait_for_database

from app import webhooks
from app.main import build_app
from app.settings import TicketingSettings

TEST_DB_NAME = "nethiz_ticketing_test"

API_KEY = "test_tkt_key"
WEBHOOK_SECRET = "test_webhook_secret"


def _build_settings() -> TicketingSettings:
    return TicketingSettings(
        company_db_user=os.environ.get("COMPANY_DB_USER", "nethiz"),
        company_db_password=os.environ.get("COMPANY_DB_PASSWORD", "nethiz_dev_pw"),
        company_db_host=os.environ.get("COMPANY_DB_HOST", "localhost"),
        company_db_port=int(os.environ.get("COMPANY_DB_PORT", "55432")),
        company_db_name=TEST_DB_NAME,
        ticketing_api_key=API_KEY,
        webhook_secret=WEBHOOK_SECRET,
        notification_api_base_url="http://notification-hub.invalid:8000",
        notify_api_key="test_notify_key",
        default_webhook_targets="",
    )


def _ensure_test_database_exists(s: TicketingSettings) -> None:
    """Best-effort: create the `_test` database if it doesn't exist yet.

    docs/contracts.md's db-init only provisions the non-test databases; this service's
    own tests need `nethiz_ticketing_test`. We never touch company/db-init (out of
    scope for this service), so we create it ourselves against the real
    `nethiz_ticketing` database, which db-init does provision.
    """
    admin_engine = make_engine(s.database_url_for("nethiz_ticketing"))
    try:
        with admin_engine.connect() as conn:
            conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DB_NAME}
            ).first()
            if exists is None:
                conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    finally:
        admin_engine.dispose()


@pytest.fixture(scope="session")
def settings() -> TicketingSettings:
    s = _build_settings()
    try:
        _ensure_test_database_exists(s)
        engine = make_engine(s.database_url)
        wait_for_database(engine, attempts=3, delay=0.5)
        engine.dispose()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test database '{TEST_DB_NAME}' not reachable: {exc}")
    return s


@pytest.fixture(scope="session", autouse=True)
def _fast_webhook_retries():
    """Tests should not wait real seconds between webhook delivery attempts."""
    original_delay = webhooks.RETRY_DELAY_SECONDS
    webhooks.RETRY_DELAY_SECONDS = 0
    yield
    webhooks.RETRY_DELAY_SECONDS = original_delay


@pytest.fixture(scope="session")
def app_instance(settings: TicketingSettings):
    return build_app(settings)


@pytest.fixture(scope="session")
def client(app_instance):
    with TestClient(app_instance) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_db(settings: TicketingSettings, client):
    """Truncate mutable tables before every test for isolation.

    Depends on `client` (not just `app_instance`) so the app's lifespan startup --
    which creates the schema/tables -- has already run before we try to truncate them.
    """
    engine = make_engine(settings.database_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE tkt.webhook_deliveries, tkt.status_history, "
                "tkt.comments, tkt.tickets RESTART IDENTITY CASCADE"
            )
        )
        conn.execute(text("ALTER SEQUENCE tkt.ticket_key_seq RESTART WITH 1"))
        conn.execute(text("DELETE FROM tkt.webhook_subscriptions"))
    engine.dispose()
    yield


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}
