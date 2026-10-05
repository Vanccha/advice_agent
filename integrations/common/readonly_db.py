"""Read-only access to the company's `diag.*` views via the `readonly_diag`
role (docs/contracts.md §1, §3).

PostgreSQL already refuses `readonly_diag` anything but `SELECT` on schema
`diag` (no rights at all on schema `core`). This module enforces the same
rule in code, before a statement ever reaches the driver: every session is
opened with `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`, and
`assert_read_only_statement` rejects anything whose first keyword isn't
`SELECT`/`WITH`.
"""
from __future__ import annotations

import datetime
import decimal
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_ALLOWED_FIRST_KEYWORDS = frozenset({"SELECT", "WITH"})

# Module-level engine cache, keyed by DSN, so repeated calls (and repeated
# `get_engine()` calls across tools within one process) share one pool.
_engines: dict[str, Engine] = {}


class ReadOnlyViolation(Exception):
    """Raised when a statement other than SELECT/WITH is attempted against the
    diagnostic database. Raised in Python, before the driver ever sees it."""


def _first_keyword(sql: str) -> str:
    """Return the first SQL keyword in `sql`, skipping leading whitespace and
    `--`/`/* */` comments."""
    remaining = sql
    while True:
        stripped = remaining.lstrip()
        if stripped.startswith("--"):
            newline = stripped.find("\n")
            remaining = stripped[newline + 1 :] if newline != -1 else ""
            continue
        if stripped.startswith("/*"):
            end = stripped.find("*/")
            remaining = stripped[end + 2 :] if end != -1 else ""
            continue
        remaining = stripped
        break

    for i, ch in enumerate(remaining):
        if not ch.isalpha():
            return remaining[:i].upper()
    return remaining.upper()


def assert_read_only_statement(sql: str) -> None:
    """Raise `ReadOnlyViolation` unless `sql` starts with SELECT or WITH."""
    keyword = _first_keyword(sql)
    if keyword not in _ALLOWED_FIRST_KEYWORDS:
        raise ReadOnlyViolation(
            f"Refusing to send a non-read statement to the diagnostic database "
            f"(first keyword was {keyword!r}, only SELECT/WITH are allowed)."
        )


def get_engine(database_url: str) -> Engine:
    """Return a process-wide cached engine for `database_url`."""
    engine = _engines.get(database_url)
    if engine is None:
        # local import keeps `create_engine` easy to monkeypatch/mock in tests
        from sqlalchemy import create_engine

        engine = create_engine(database_url, pool_pre_ping=True, future=True)
        _engines[database_url] = engine
    return engine


@contextmanager
def read_only_session(database_url: str) -> Iterator[Session]:
    """A SQLAlchemy session whose transaction is marked READ ONLY at the
    Postgres level for its whole lifetime."""
    engine = get_engine(database_url)
    session_factory = sessionmaker(bind=engine, future=True)
    session = session_factory()
    try:
        session.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
        yield session
        session.rollback()  # nothing here should ever need a commit
    finally:
        session.close()


def fetch_all(database_url: str, sql: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """Run a SELECT/WITH statement and return JSON-safe dict rows."""
    assert_read_only_statement(sql)
    with read_only_session(database_url) as session:
        result = session.execute(text(sql), params or {})
        rows = [dict(row._mapping) for row in result]
    return rows_to_dicts(rows)


def fetch_one(database_url: str, sql: str, params: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """Like `fetch_all` but returns the first row (or `None`)."""
    rows = fetch_all(database_url, sql, params)
    return rows[0] if rows else None


def rows_to_dicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Make `Decimal`/`datetime`/`date` values JSON-safe (float / ISO-8601 str)."""
    return [{key: _json_safe(value) for key, value in row.items()} for row in rows]


def _json_safe(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value
