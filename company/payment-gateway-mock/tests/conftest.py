from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from shared.app_factory import create_app
from shared.auth import ApiKeyRegistry
from shared.db import make_engine, make_session_factory, session_scope, wait_for_database

from app.bootstrap import bootstrap
from app.metrics_gauges import install_psp_metrics
from app.models import SCHEMA
from app.outage import install_outage_guard
from app.routers import charges as charges_router
from app.routers import control as control_router
from app.settings import Settings

TEST_DB_NAME = "netswift_payment_test"
TEST_API_KEY = "test_psp_key_do_not_use_in_prod"
TEST_WEBHOOK_SECRET = "test_webhook_secret"


def _ensure_test_database(settings: Settings) -> None:
    admin_engine = create_engine(settings.database_url_for("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": TEST_DB_NAME},
            ).first()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    finally:
        admin_engine.dispose()


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        company_db_name=TEST_DB_NAME,
        psp_api_key=TEST_API_KEY,
        webhook_secret=TEST_WEBHOOK_SECRET,
        core_webhook_url="http://core-api.test/v1/webhooks/payment",
    )


@pytest.fixture(scope="session")
def db_engine(settings: Settings):
    probe = Settings()
    try:
        probe_engine = create_engine(probe.database_url_for("postgres"))
        with probe_engine.connect():
            pass
        probe_engine.dispose()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"company database is not reachable: {exc}")

    _ensure_test_database(settings)
    engine = make_engine(settings.database_url)
    wait_for_database(engine, attempts=10, delay=0.5)
    bootstrap(engine, settings)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def session_factory(db_engine):
    return make_session_factory(db_engine)


@pytest.fixture(autouse=True)
def _clean_tables(session_factory, settings):
    with session_scope(session_factory) as session:
        session.execute(text(f"TRUNCATE TABLE {SCHEMA}.webhook_deliveries RESTART IDENTITY CASCADE"))
        session.execute(text(f"TRUNCATE TABLE {SCHEMA}.refunds RESTART IDENTITY CASCADE"))
        session.execute(text(f"TRUNCATE TABLE {SCHEMA}.charges RESTART IDENTITY CASCADE"))
        session.execute(
            text(f"UPDATE {SCHEMA}.control_flags SET value = 'false'::jsonb WHERE key = 'outage'")
        )
        session.execute(
            text(f"UPDATE {SCHEMA}.control_flags SET value = to_jsonb(:rate) WHERE key = 'failure_rate'"),
            {"rate": settings.psp_failure_rate},
        )
        session.execute(
            text(f"UPDATE {SCHEMA}.control_flags SET value = '0'::jsonb WHERE key = 'latency_ms'")
        )
        session.execute(
            text(f"UPDATE {SCHEMA}.control_flags SET value = 'null'::jsonb WHERE key = 'force_failure_code'")
        )
    yield


@pytest.fixture
def app(session_factory, settings):
    registry = ApiKeyRegistry()
    registry.register(settings.psp_api_key, "psp-client", {"psp:*"})

    fastapi_app = create_app(
        service_name=settings.service_name,
        version=settings.service_version,
        title="payment-gateway-mock (test)",
    )
    fastapi_app.include_router(charges_router.build_router(session_factory, settings, registry))
    fastapi_app.include_router(control_router.build_router(session_factory, settings, registry))
    install_psp_metrics(fastapi_app)
    install_outage_guard(
        fastapi_app,
        session_factory,
        service_name=settings.service_name,
        service_version=settings.service_version,
    )
    return fastapi_app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def auth_headers(settings):
    return {"X-API-Key": settings.psp_api_key}
