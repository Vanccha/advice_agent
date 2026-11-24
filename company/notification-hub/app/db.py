"""Engine and request-scoped session for the notification hub.

Where this sits: `get_session` is the dependency used by every router.

Key ideas:
- Commit on success, roll back on error, always close.

Read next: `models.py`.
"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from shared.db import make_engine, make_session_factory

from app.settings import get_settings

_settings = get_settings()
engine = make_engine(_settings.database_url)
SessionFactory = make_session_factory(engine)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency: a transactional session, committed on success."""
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
