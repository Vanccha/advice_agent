"""Idempotent startup: schema, tables and default control flags.

Where this sits: called from the app's startup hook.

Key ideas:
- Control flags live in the database, not in memory, so every worker process
  sees the same outage state and it survives restarts.
- Only missing flags are seeded, so an operator's setting is never overwritten
  by a restart.

Read next: `engine.py`.
"""
from __future__ import annotations

import logging

from sqlalchemy.engine import Engine

from shared.db import Base, execute_sql_statements, session_scope, wait_for_database, make_session_factory

from app import models  # noqa: F401  (ensures models are registered on Base.metadata)
from app.models import SCHEMA, ControlFlag
from app.settings import Settings

logger = logging.getLogger(__name__)

DEFAULT_CONTROL_FLAGS = {
    "outage": False,
    "latency_ms": 0,
    "force_failure_code": None,
}


def bootstrap(engine: Engine, settings: Settings) -> None:
    """Idempotent startup: wait for DB, create schema/tables, seed control flags."""
    wait_for_database(engine)
    execute_sql_statements(engine, [f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"])

    # Bind tables that live in other schemas' metadata is not an issue here: this
    # service only ever creates its own `psp` schema tables.
    Base.metadata.create_all(
        bind=engine,
        tables=[t for t in Base.metadata.tables.values() if t.schema == SCHEMA],
    )

    factory = make_session_factory(engine)
    with session_scope(factory) as session:
        seed_defaults = {
            "failure_rate": settings.psp_failure_rate,
            **DEFAULT_CONTROL_FLAGS,
        }
        for key, value in seed_defaults.items():
            existing = session.get(ControlFlag, key)
            if existing is None:
                session.add(ControlFlag(key=key, value=value))
    logger.info("payment-gateway-mock bootstrap complete")
