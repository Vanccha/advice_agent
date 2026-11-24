"""SQLAlchemy engine, session and idempotent-DDL helpers shared by every service.

Where this sits: each service's `db.py` builds its own engine from these
helpers; every service owns its own database.

Key ideas:
- Unit of work: `session_scope` commits on success and rolls back on any
  failure, so a request never leaves half a change behind.
- `pool_pre_ping` detects connections killed by a database restart before
  they are handed to a request.
- Startup DDL must be idempotent (safe to run on every boot) because
  containers restart at any time.

Read next: `settings.py`.
"""
from __future__ import annotations

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for every NetSwift service schema."""


def make_engine(database_url: str, **kwargs: object) -> Engine:
    """Build a pooled engine that pings connections before use."""
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        future=True,
        **kwargs,  # type: ignore[arg-type]
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Session factory with explicit flushing and objects that stay usable after commit."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Transactional scope: commit on success, roll back on failure."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def wait_for_database(engine: Engine, attempts: int = 30, delay: float = 1.0) -> None:
    """Block until the database answers, so startup survives the container start-order race."""
    import time

    last: Exception | None = None
    for _ in range(attempts):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # pragma: no cover - startup race
            last = exc
            time.sleep(delay)
    raise RuntimeError(f"database not reachable: {last}")


def execute_sql_statements(engine: Engine, statements: Iterable[str]) -> None:
    """Run idempotent DDL/DCL statements, each in its own transaction."""
    for statement in statements:
        stripped = statement.strip()
        if not stripped:
            continue
        with engine.begin() as conn:
            conn.execute(text(stripped))


def execute_sql_file(engine: Engine, path: Path) -> None:
    """Run a file of `;`-separated idempotent statements."""
    sql = path.read_text(encoding="utf-8")
    execute_sql_statements(engine, sql.split(";\n"))
