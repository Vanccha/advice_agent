from __future__ import annotations

from functools import lru_cache

from shared.settings import CompanySettings


class NotificationHubSettings(CompanySettings):
    """Settings for the notification-hub service."""

    service_name: str = "notification-hub"
    service_version: str = "1.0.0"
    company_db_name: str = "nethiz_notify"

    # Plain API key clients must send as X-API-Key. Stored hashed in the in-memory registry.
    notify_api_key: str = "nethiz_notify_key_change_me"


@lru_cache
def get_settings() -> NotificationHubSettings:
    return NotificationHubSettings()
