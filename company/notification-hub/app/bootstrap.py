from __future__ import annotations

import logging

from sqlalchemy.engine import Engine

from shared.db import execute_sql_statements, make_engine, make_session_factory, session_scope, wait_for_database

from app.models import SCHEMA, Base, Channel
from app.settings import get_settings

logger = logging.getLogger(__name__)

# (slug, display_name, description) — display names per docs/contracts.md §1.3/§2.6.
CHANNEL_SEEDS: list[tuple[str, str, str]] = [
    ("technical-infra", "Technical Infrastructure", "Infrastructure and technical service alerts."),
    ("billing", "Billing", "Payment and billing notifications."),
    ("subscription-ops", "Subscription Operations", "Subscription status changes."),
    ("field-install", "Field Installation Team", "Installation and field operations."),
    ("ops-general", "Operations General", "General operations announcements and the default channel."),
]


def run_bootstrap() -> None:
    """Idempotent startup: wait for DB, create schema/tables, seed channels.

    Safe to call more than once (used directly by on_startup, and again by tests
    that assert idempotency).
    """
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        wait_for_database(engine)
        execute_sql_statements(engine, [f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"])
        Base.metadata.create_all(engine)
        _seed_channels(engine)
    finally:
        engine.dispose()
    logger.info("notification-hub bootstrap complete")


def _seed_channels(engine: Engine) -> None:
    factory = make_session_factory(engine)
    with session_scope(factory) as session:
        for slug, display_name, description in CHANNEL_SEEDS:
            existing = session.get(Channel, slug)
            if existing is None:
                session.add(Channel(slug=slug, display_name=display_name, description=description))
            else:
                existing.display_name = display_name
                existing.description = description
