from __future__ import annotations

import os

# Sane local defaults; inside the test-runner container the real compose
# environment already provides all of these (company-db, core-api, payment-gateway).
os.environ.setdefault("COMPANY_DB_HOST", "company-db")
os.environ.setdefault("COMPANY_DB_PORT", "5432")
os.environ.setdefault("COMPANY_DB_USER", "netswift")
os.environ.setdefault("COMPANY_DB_PASSWORD", "netswift_dev_pw")
os.environ.setdefault("CORE_API_BASE_URL", "http://core-api:8000")
os.environ.setdefault("CORE_API_KEY_CRM", "netswift_crm_key_change_me")
os.environ.setdefault("PAYMENT_API_BASE_URL", "http://payment-gateway:8000")
os.environ.setdefault("PSP_API_KEY", "netswift_psp_key_change_me")
os.environ.setdefault("PSP_FAILURE_RATE", "0.08")

import httpx
import pytest
from sqlalchemy import create_engine, text

from chaos import scenarios as sc
from chaos.settings import ChaosSettings, get_settings


def _db_reachable(url: str) -> bool:
    try:
        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def _http_reachable(base_url: str) -> bool:
    try:
        resp = httpx.get(f"{base_url}/health", timeout=3.0)
        return resp.status_code < 500
    except Exception:
        return False


@pytest.fixture(scope="session")
def settings() -> ChaosSettings:
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture(scope="session")
def live_stack_available(settings: ChaosSettings) -> bool:
    return (
        _db_reachable(settings.database_url)
        and _db_reachable(settings.payment_database_url)
        and _http_reachable(settings.core_api_base_url)
        and _http_reachable(settings.payment_api_base_url)
    )


@pytest.fixture()
def require_live_stack(live_stack_available: bool) -> None:
    if not live_stack_available:
        pytest.skip("live company stack (company-db/core-api/payment-gateway) is not reachable")


@pytest.fixture()
def core_eng(require_live_stack, settings: ChaosSettings):
    from chaos.db import core_engine

    return core_engine(settings)


@pytest.fixture()
def payment_eng(require_live_stack, settings: ChaosSettings):
    from chaos.db import payment_engine

    return payment_engine(settings)


def _reset_everything(settings: ChaosSettings, core_eng, payment_eng) -> None:
    sc.reset_stuck_provisioning(core_eng)
    sc.reset_paid_not_active(core_eng)
    sc.reset_regional_outage(core_eng)
    sc.reset_double_charge(core_eng, payment_eng)
    sc.reset_missed_installation(core_eng)
    try:
        sc.reset_payment_down(settings)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _clean_chaos_state(request, live_stack_available, settings):
    """Every test starts and ends with a clean slate, regardless of outcome."""
    if not live_stack_available:
        yield
        return
    from chaos.db import core_engine, payment_engine

    core_eng = core_engine(settings)
    payment_eng = payment_engine(settings)
    _reset_everything(settings, core_eng, payment_eng)
    yield
    _reset_everything(settings, core_eng, payment_eng)


def table_counts(conn, tables: list[str]) -> dict[str, int]:
    counts = {}
    for t in tables:
        counts[t] = conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
    return counts


CORE_TABLES = [
    "core.customers",
    "core.subscriptions",
    "core.payments",
    "core.provisioning_jobs",
    "core.modems",
    "core.installation_appointments",
    "core.network_incidents",
    "core.incident_subscriptions",
]

PAYMENT_TABLES = ["psp.charges"]
