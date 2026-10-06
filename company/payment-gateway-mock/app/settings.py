from __future__ import annotations

from functools import lru_cache

from shared.settings import CompanySettings


class Settings(CompanySettings):
    """Settings for the payment-gateway-mock (PSP simulator) service."""

    service_name: str = "payment-gateway"
    service_version: str = "1.0.0"
    company_db_name: str = "netswift_payment"

    psp_api_key: str = "netswift_psp_key_change_me"
    psp_failure_rate: float = 0.08
    webhook_secret: str = "netswift_webhook_secret_change_me"
    core_webhook_url: str = "http://core-api:8000/v1/webhooks/payment"


@lru_cache
def get_settings() -> Settings:
    return Settings()
