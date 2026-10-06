from __future__ import annotations

from functools import lru_cache

from shared.settings import CompanySettings


class TicketingSettings(CompanySettings):
    """Settings for the ticketing service, extending the shared company base."""

    service_name: str = "ticketing"
    service_version: str = "1.0.0"
    company_db_name: str = "netswift_ticketing"

    ticketing_api_key: str = ""
    webhook_secret: str = ""

    notification_api_base_url: str = "http://notification-hub:8000"
    notify_api_key: str = ""

    default_webhook_targets: str = ""

    @property
    def default_webhook_target_list(self) -> list[str]:
        return [u.strip() for u in self.default_webhook_targets.split(",") if u.strip()]


@lru_cache
def get_settings() -> TicketingSettings:
    return TicketingSettings()
