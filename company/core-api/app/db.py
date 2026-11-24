"""Engine and session wiring for core-api.

Where this sits: `get_db` is the FastAPI dependency every router uses.

Key ideas:
- One session per request: committed when the handler returns, rolled back
  if it raises, so partial writes never survive an error.
- The engine is created lazily on first use, so importing the app does not
  need a database.

Read next: `models.py`.
"""
from __future__ import annotations

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from shared.db import make_engine, make_session_factory

from app.settings import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Create the engine on first use."""
    global _engine
    if _engine is None:
        _engine = make_engine(get_settings().database_url)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Create the session factory on first use."""
    global _session_factory
    if _session_factory is None:
        _session_factory = make_session_factory(get_engine())
    return _session_factory


def get_db():
    """FastAPI dependency yielding a request-scoped session."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
