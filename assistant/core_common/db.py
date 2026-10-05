"""Engine/session plumbing for `ASSISTANT_DATABASE_URL` (schema `asst`).

Import as: ``from core_common.db import Base, session_scope, get_engine, bootstrap_schema``.
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from core_common.models import Base
from core_common.settings import get_settings

__all__ = [
    "Base",
    "get_engine",
    "create_sqlite_engine",
    "get_sessionmaker",
    "session_scope",
    "wait_for_database",
    "bootstrap_schema",
    "AUDIT_APPEND_ONLY_TRIGGER_SQL",
]

# The append-only guarantee for `asst.audit_entries` (contracts §1.5): any UPDATE or DELETE
# raises instead of silently mutating/removing a row. Applied only on Postgres — SQLite
# (used by unit tests) has no equivalent trigger mechanism we rely on here.
AUDIT_APPEND_ONLY_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION asst.audit_entries_append_only() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'asst.audit_entries is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_entries_no_update_delete ON asst.audit_entries;

CREATE TRIGGER audit_entries_no_update_delete
BEFORE UPDATE OR DELETE ON asst.audit_entries
FOR EACH ROW EXECUTE FUNCTION asst.audit_entries_append_only();
"""


def _build_engine(database_url: str) -> Engine:
    from sqlalchemy import create_engine

    engine = create_engine(database_url, future=True)
    if database_url.startswith("sqlite"):
        # SQLite has no schemas; map the `asst` schema onto the default (no-schema) namespace
        # so the very same ORM models work for in-memory unit tests.
        engine = engine.execution_options(schema_translate_map={"asst": None})
    return engine


@lru_cache(maxsize=None)
def get_engine(database_url: str | None = None) -> Engine:
    """Cached engine factory. Pass an explicit URL in tests; otherwise uses settings."""
    url = database_url or get_settings().ASSISTANT_DATABASE_URL
    if not url:
        raise RuntimeError("ASSISTANT_DATABASE_URL is not set")
    return _build_engine(url)


def create_sqlite_engine(url: str = "sqlite:///:memory:") -> Engine:
    """Uncached, isolated SQLite engine — for unit tests that need a fresh schema each time."""
    return _build_engine(url)


def get_sessionmaker(database_url: str | None = None) -> sessionmaker[Session]:
    engine = get_engine(database_url)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(session_factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """Commit on success, rollback on exception, always close."""
    factory = session_factory or get_sessionmaker()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def wait_for_database(database_url: str | None = None, timeout: float = 30.0, interval: float = 1.0) -> None:
    """Block until the database accepts connections, or raise TimeoutError."""
    engine = get_engine(database_url)
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # pragma: no cover - depends on live infra
            last_exc = exc
            time.sleep(interval)
    raise TimeoutError(f"Database not reachable after {timeout}s: {last_exc}")


def bootstrap_schema(engine: Engine) -> None:
    """Create schema `asst`, every table of contracts §1.5, and the append-only trigger."""
    is_postgres = engine.dialect.name == "postgresql"
    with engine.begin() as conn:
        if is_postgres:
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS asst"))
        Base.metadata.create_all(conn)
        if is_postgres:
            # exec_driver_sql (not execute(text(...))) so the driver can run the
            # semicolon-separated DDL statements as a single batch.
            conn.exec_driver_sql(AUDIT_APPEND_ONLY_TRIGGER_SQL)
