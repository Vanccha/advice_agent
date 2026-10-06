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
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        future=True,
        **kwargs,  # type: ignore[arg-type]
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
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
    sql = path.read_text(encoding="utf-8")
    execute_sql_statements(engine, sql.split(";\n"))
