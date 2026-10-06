from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy.engine import Connection, Engine

from shared.db import make_engine

from chaos.settings import ChaosSettings

_core_engine: Engine | None = None
_payment_engine: Engine | None = None


def core_engine(settings: ChaosSettings) -> Engine:
    global _core_engine
    if _core_engine is None:
        _core_engine = make_engine(settings.database_url)
    return _core_engine


def payment_engine(settings: ChaosSettings) -> Engine:
    global _payment_engine
    if _payment_engine is None:
        _payment_engine = make_engine(settings.payment_database_url)
    return _payment_engine


@contextmanager
def core_tx(settings: ChaosSettings) -> Generator[Connection, None, None]:
    """One transaction against netswift_core. Commits on success, rolls back on error."""
    engine = core_engine(settings)
    with engine.begin() as conn:
        yield conn


@contextmanager
def payment_tx(settings: ChaosSettings) -> Generator[Connection, None, None]:
    """One transaction against netswift_payment."""
    engine = payment_engine(settings)
    with engine.begin() as conn:
        yield conn


def reset_engines() -> None:
    """Dispose cached engines (used by tests between scenarios)."""
    global _core_engine, _payment_engine
    if _core_engine is not None:
        _core_engine.dispose()
        _core_engine = None
    if _payment_engine is not None:
        _payment_engine.dispose()
        _payment_engine = None
