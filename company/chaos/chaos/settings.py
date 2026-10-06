from __future__ import annotations

from functools import lru_cache

from shared.settings import CompanySettings


class ChaosSettings(CompanySettings):
    """Settings for the chaos CLI: two DB connections plus two HTTP upstreams."""

    service_name: str = "chaos"

    # netswift_core is the base CompanySettings default already.
    payment_db_name: str = "netswift_payment"

    core_api_base_url: str = "http://core-api:8000"
    core_api_key_crm: str = "netswift_crm_key_change_me"

    payment_api_base_url: str = "http://payment-gateway:8000"
    psp_api_key: str = "netswift_psp_key_change_me"

    # Default failure rate the PSP control flags are restored to on `reset`,
    # i.e. the same env var the payment-gateway-mock itself seeds from.
    psp_failure_rate: float = 0.08

    @property
    def payment_database_url(self) -> str:
        return self.database_url_for(self.payment_db_name)


@lru_cache
def get_settings() -> ChaosSettings:
    return ChaosSettings()
