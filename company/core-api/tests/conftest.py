from __future__ import annotations

import os

# Must run before any `app.*` import: settings are read from env at import time.
os.environ.setdefault("COMPANY_DB_HOST", "localhost")
os.environ.setdefault("COMPANY_DB_PORT", "5432")
os.environ.setdefault("COMPANY_DB_USER", "netswift")
os.environ.setdefault("COMPANY_DB_PASSWORD", "netswift_dev_pw")
os.environ["COMPANY_DB_NAME"] = "netswift_core_test"
os.environ.setdefault("CORE_API_KEY_CRM", "netswift_crm_key_change_me")
os.environ.setdefault("CORE_API_KEY_PARTNER", "netswift_partner_key_change_me")
os.environ.setdefault("WEBHOOK_SECRET", "netswift_webhook_secret_change_me")
os.environ.setdefault("READONLY_DIAG_USER", "readonly_diag")
os.environ.setdefault("READONLY_DIAG_PASSWORD", "diag_dev_pw")
os.environ.setdefault("PSP_API_KEY", "netswift_psp_key_change_me")
os.environ.setdefault("CREDIT_MAX_PER_REQUEST_GBP", "25")
os.environ.setdefault("RESEND_COOLDOWN_SECONDS", "120")
os.environ.setdefault("PAYMENT_API_BASE_URL", "http://payment-gateway:8000")
os.environ["SEED_ON_STARTUP"] = "false"

import pytest
from sqlalchemy import create_engine, text

CRM_KEY = os.environ["CORE_API_KEY_CRM"]
PARTNER_KEY = os.environ["CORE_API_KEY_PARTNER"]


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


def ensure_database(name: str) -> None:
    engine = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
        ).first()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine.dispose()


@pytest.fixture(scope="session")
def require_db():
    if not DB_AVAILABLE:
        pytest.skip("company-db is not reachable; skipping database-backed tests")


@pytest.fixture(scope="session")
def app_client(require_db):
    ensure_database("netswift_core_test")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def client(app_client):
    return app_client


@pytest.fixture()
def crm_headers():
    return {"X-API-Key": CRM_KEY}


@pytest.fixture()
def partner_headers():
    return {"X-API-Key": PARTNER_KEY}
