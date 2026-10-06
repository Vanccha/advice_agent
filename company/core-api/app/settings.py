from __future__ import annotations

from functools import lru_cache

from shared.settings import CompanySettings


class CoreApiSettings(CompanySettings):
    """Settings for the core-api service."""

    service_name: str = "core-api"
    service_version: str = "1.0.0"

    core_api_key_crm: str = "netswift_crm_key_change_me"
    core_api_key_partner: str = "netswift_partner_key_change_me"

    readonly_diag_user: str = "readonly_diag"
    readonly_diag_password: str = "diag_dev_pw"

    payment_api_base_url: str = "http://payment-gateway:8000"
    psp_api_key: str = "netswift_psp_key_change_me"

    webhook_secret: str = "netswift_webhook_secret_change_me"

    credit_max_per_request_gbp: float = 25.0
    resend_cooldown_seconds: int = 120

    seed_on_startup: bool = True


@lru_cache
def get_settings() -> CoreApiSettings:
    return CoreApiSettings()
