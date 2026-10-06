import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from common import readonly_db
from common.readonly_db import ReadOnlyViolation, assert_read_only_statement, fetch_all, rows_to_dicts


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM diag.customer_overview",
        "  select customer_no from diag.customer_overview",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "-- a leading comment\nSELECT 1",
        "/* block comment */ SELECT 1",
    ],
)
def test_allows_select_and_with(sql: str) -> None:
    assert_read_only_statement(sql)  # must not raise


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO diag.customer_overview DEFAULT VALUES",
        "UPDATE core.customers SET full_name='x'",
        "DELETE FROM core.customers",
        "DROP TABLE core.customers",
        "   update core.customers set x=1",
        "GRANT ALL ON core.customers TO someone",
    ],
)
def test_rejects_non_select_statements(sql: str) -> None:
    with pytest.raises(ReadOnlyViolation):
        assert_read_only_statement(sql)


def test_fetch_all_rejects_before_touching_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-SELECT statement must never reach `get_engine`/the driver at all."""

    def _boom(_url: str):
        raise AssertionError("get_engine must not be called for a rejected statement")

    monkeypatch.setattr(readonly_db, "get_engine", _boom)
    with pytest.raises(ReadOnlyViolation):
        fetch_all("postgresql://example/ignored", "DELETE FROM core.customers")


def test_rows_to_dicts_makes_decimal_and_datetime_json_safe() -> None:
    import datetime
    from decimal import Decimal

    rows = [{"amount_gbp": Decimal("349.00"), "created_at": datetime.datetime(2026, 1, 1, 12, 0, 0), "status": "ok"}]
    safe = rows_to_dicts(rows)
    assert safe == [{"amount_gbp": 349.0, "created_at": "2026-01-01T12:00:00", "status": "ok"}]
    assert isinstance(safe[0]["amount_gbp"], float)


@pytest.fixture
def diag_database_url() -> str:
    url = os.environ.get("DIAG_DATABASE_URL")
    if not url:
        pytest.skip("DIAG_DATABASE_URL not set; skipping live readonly_diag test")
    return url


def test_live_select_against_diag_view_succeeds(diag_database_url: str) -> None:
    try:
        rows = fetch_all(diag_database_url, "SELECT customer_no FROM diag.customer_overview LIMIT 1")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"company-db not reachable: {exc}")
    assert len(rows) == 1
    assert rows[0]["customer_no"].startswith("NS-")


def test_live_readonly_role_cannot_select_base_tables(diag_database_url: str) -> None:
    """Belt-and-suspenders: even if our own guard were bypassed, Postgres itself
    refuses `readonly_diag` any access to schema `core` (docs/contracts.md §1)."""
    try:
        with readonly_db.read_only_session(diag_database_url) as session:
            with pytest.raises(DBAPIError):
                session.execute(text("SELECT * FROM core.customers LIMIT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"company-db not reachable: {exc}")


def test_live_readonly_role_cannot_write(diag_database_url: str) -> None:
    try:
        with readonly_db.read_only_session(diag_database_url) as session:
            with pytest.raises(DBAPIError):
                session.execute(text("INSERT INTO diag.customer_overview DEFAULT VALUES"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"company-db not reachable: {exc}")
