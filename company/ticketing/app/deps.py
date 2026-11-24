"""FastAPI dependency that provides a request-scoped database session.

Where this sits: used by every route in `routers/`.

Key ideas:
- The session factory lives on `app.state`, not in a module global, so each app
  instance (production or test) can point at its own database.
- Commit on success, roll back on error, always close.

Read next: `main.py`.
"""
from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session


def get_session(request: Request) -> Generator[Session, None, None]:
    """Yield a transactional session bound to the app's session factory.

    The factory lives on ``app.state`` so each app instance (production or test) can
    point at its own database without relying on process-wide globals.
    """
    factory = request.app.state.session_factory
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
