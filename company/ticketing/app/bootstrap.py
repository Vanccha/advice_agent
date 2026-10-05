from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from shared.db import Base, wait_for_database
from app import models  # noqa: F401 - registers tables on Base.metadata
from app.keys import SEQUENCE_NAME
from app.settings import TicketingSettings

logger = logging.getLogger(__name__)

DEPARTMENTS = [
    # code, display_name (Turkish), email, channel_slug
    ("TECHNICAL_INFRA", "Teknik Altyapı", "teknik-altyapi@ornek-eposta.test", "teknik-altyapi"),
    ("BILLING", "Faturalama", "faturalama@ornek-eposta.test", "faturalama"),
    ("SUBSCRIPTION_OPS", "Abonelik İşlemleri", "abonelik-islemleri@ornek-eposta.test", "abonelik-islemleri"),
    ("FIELD_INSTALL", "Saha Kurulum Ekibi", "saha-kurulum@ornek-eposta.test", "saha-kurulum"),
]

DEFAULT_WEBHOOK_EVENTS = ["ticket.created", "ticket.status_changed", "ticket.commented"]


def run_bootstrap(engine: Engine, settings: TicketingSettings) -> None:
    """Idempotent startup: wait for DB, create schema/tables, seed reference data."""
    wait_for_database(engine)

    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS tkt"))

    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(text(f"CREATE SEQUENCE IF NOT EXISTS {SEQUENCE_NAME} START WITH 1"))

        for code, display_name, email, channel_slug in DEPARTMENTS:
            conn.execute(
                text(
                    """
                    INSERT INTO tkt.departments (code, display_name, email, channel_slug)
                    VALUES (:code, :display_name, :email, :channel_slug)
                    ON CONFLICT (code) DO UPDATE SET
                        display_name = EXCLUDED.display_name,
                        email = EXCLUDED.email,
                        channel_slug = EXCLUDED.channel_slug
                    """
                ),
                {"code": code, "display_name": display_name, "email": email, "channel_slug": channel_slug},
            )

        for url in settings.default_webhook_target_list:
            existing = conn.execute(
                text("SELECT id FROM tkt.webhook_subscriptions WHERE target_url = :url"),
                {"url": url},
            ).first()
            if existing is not None:
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO tkt.webhook_subscriptions
                        (name, target_url, events, is_active, secret, created_at)
                    VALUES (:name, :url, :events, true, :secret, now())
                    """
                ),
                {
                    "name": f"default:{url}",
                    "url": url,
                    "events": DEFAULT_WEBHOOK_EVENTS,
                    "secret": settings.webhook_secret,
                },
            )

    logger.info("ticketing bootstrap complete")
