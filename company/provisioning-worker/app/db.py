"""Engine and session factory for the worker.

Where this sits: used by the polling loop in `main.py`.

Read next: `worker.py`.
"""
from __future__ import annotations

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from shared.db import make_engine, make_session_factory, wait_for_database

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


def wait_ready() -> None:
    """Block until the database answers."""
    wait_for_database(get_engine())
