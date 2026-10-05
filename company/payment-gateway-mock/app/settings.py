from __future__ import annotations

from functools import lru_cache

from shared.settings import CompanySettings


class Settings(CompanySettings):
    """Settings for the payment-gateway-mock (PSP simulator) service."""

    service_name: str = "payment-gateway"
    service_version: str = "1.0.0"
    company_db_name: str = "nethiz_payment"

    psp_api_key: str = "nethiz_psp_key_change_me"
    psp_failure_rate: float = 0.08
    webhook_secret: str = "nethiz_webhook_secret_change_me"
    core_webhook_url: str = "http://core-api:8000/v1/webhooks/payment"


@lru_cache
def get_settings() -> Settings:
    return Settings()
