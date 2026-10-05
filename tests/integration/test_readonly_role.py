"""The integration layer physically cannot write to the company database.

This is the load-bearing claim of the product story, so it is asserted against the real
PostgreSQL role rather than against application code that could be bypassed.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

pytestmark = pytest.mark.integration

PII_COLUMNS = {"national_id", "address_line", "card_number", "iban", "card_token"}

WRITE_ATTEMPTS = [
    ("update core.subscriptions set status = 'active' where id = 1", "update a core table"),
    (
        "insert into core.credits (subscription_id, amount_try, reason, created_by) "
        "values (1, 10, 'x', 'y')",
        "insert into a core table",
    ),
    ("delete from core.payments where id = 1", "delete from a core table"),
    ("create table core.sneaky (id int)", "create a table"),
]


@pytest.fixture(scope="module")
def diag_engine(diag_dsn: str):
    engine = create_engine(diag_dsn, future=True, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"diagnostic role cannot connect: {exc}")
    yield engine
    engine.dispose()


def test_diagnostic_views_are_readable(diag_engine) -> None:
    with diag_engine.connect() as conn:
        count = conn.execute(text("select count(*) from diag.subscription_status")).scalar()
    assert count and count > 0, "diagnostic views should expose the company's live data"


def test_diagnostic_views_expose_no_personal_identifiers(diag_engine) -> None:
    with diag_engine.connect() as conn:
        columns = {
            row[0]
            for row in conn.execute(
                text("select column_name from information_schema.columns where table_schema = 'diag'")
            )
        }
    leaked = columns & PII_COLUMNS
    assert not leaked, f"diagnostic views must not carry identity data: {sorted(leaked)}"


def test_base_tables_are_unreachable(diag_engine) -> None:
    with diag_engine.connect() as conn:
        with pytest.raises(ProgrammingError):
            conn.execute(text("select count(*) from core.customers"))


@pytest.mark.parametrize("statement,label", WRITE_ATTEMPTS, ids=[a[1] for a in WRITE_ATTEMPTS])
def test_every_write_is_refused_by_the_database(diag_engine, statement: str, label: str) -> None:
    with diag_engine.connect() as conn:
        with pytest.raises(ProgrammingError):
            conn.execute(text(statement))
