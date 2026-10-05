from __future__ import annotations

from sqlalchemy import text

EXPECTED_VIEWS = {
    "customer_overview",
    "subscription_status",
    "payment_status",
    "provisioning_status",
    "installation_status",
    "modem_status",
    "active_incidents",
    "incident_affected",
    "notification_history",
    "subscription_timeline",
    "region_health",
}

FORBIDDEN_COLUMNS = {"national_id", "address_line", "card_last4", "card_token"}


def test_all_diag_views_exist(app_client):
    from app.db import get_session_factory

    session = get_session_factory()()
    try:
        rows = session.execute(
            text("SELECT table_name FROM information_schema.views WHERE table_schema = 'diag'")
        ).all()
        found = {r[0] for r in rows}
        missing = EXPECTED_VIEWS - found
        assert not missing, f"missing diag views: {missing}"
    finally:
        session.close()


def test_diag_views_expose_no_pii_columns(app_client):
    from app.db import get_session_factory

    session = get_session_factory()()
    try:
        rows = session.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'diag'"
            )
        ).all()
        offending = [
            (table, column)
            for table, column in rows
            if column.lower() in FORBIDDEN_COLUMNS
        ]
        assert not offending, f"diag views leak sensitive columns: {offending}"
    finally:
        session.close()


def test_readonly_diag_role_has_no_rights_on_core_schema(app_client):
    from sqlalchemy import create_engine

    from app.settings import get_settings

    settings = get_settings()
    diag_url = (
        f"postgresql+psycopg://{settings.readonly_diag_user}:{settings.readonly_diag_password}"
        f"@{settings.company_db_host}:{settings.company_db_port}/{settings.company_db_name}"
    )
    engine = create_engine(diag_url)
    try:
        with engine.connect() as conn:
            import pytest
            from sqlalchemy.exc import ProgrammingError

            with pytest.raises(ProgrammingError):
                conn.execute(text("SELECT * FROM core.customers LIMIT 1"))
    finally:
        engine.dispose()


def test_readonly_diag_role_can_select_diag_views(app_client):
    from sqlalchemy import create_engine

    from app.settings import get_settings

    settings = get_settings()
    diag_url = (
        f"postgresql+psycopg://{settings.readonly_diag_user}:{settings.readonly_diag_password}"
        f"@{settings.company_db_host}:{settings.company_db_port}/{settings.company_db_name}"
    )
    engine = create_engine(diag_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT * FROM diag.customer_overview LIMIT 1"))
    finally:
        engine.dispose()
