"""Engine and session wiring for core-api.

Where this sits: `get_db` is the FastAPI dependency every router uses.

Key ideas:
- One session per request: committed when the handler returns, rolled back
  if it raises, so partial writes never survive an error.
- Commit before responding: the dependency is declared with
  `scope="function"` (see `get_db`) so a client never sees an id that is not
  yet committed.
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
    """FastAPI dependency yielding a request-scoped session.

    Always declare it as ``Depends(get_db, scope="function")``. With the default scope,
    FastAPI runs the code after ``yield`` (our ``commit``) only *after* the response has
    been sent. A client that creates a customer and immediately uses the returned
    ``customer_no`` in its next request can then arrive before the commit and get
    "Customer not found". ``scope="function"`` commits before the response leaves, so
    "I got a 201" always means "it is in the database".

    Lesson: this behaviour changed between FastAPI releases. An unpinned dependency
    (``fastapi>=0.115``) silently picked up the new default; pin what you have tested.
    """
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
