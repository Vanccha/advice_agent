from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class CompanySettings(BaseSettings):
    """Base settings every NetSwift service shares."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    service_name: str = "netswift-service"
    service_version: str = "1.0.0"
    log_level: str = "INFO"

    company_db_user: str = "netswift"
    company_db_password: str = "netswift_dev_pw"
    company_db_host: str = "localhost"
    company_db_port: int = 5432
    company_db_name: str = "netswift_core"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.company_db_user}:{self.company_db_password}"
            f"@{self.company_db_host}:{self.company_db_port}/{self.company_db_name}"
        )

    def database_url_for(self, db_name: str) -> str:
        return (
            f"postgresql+psycopg://{self.company_db_user}:{self.company_db_password}"
            f"@{self.company_db_host}:{self.company_db_port}/{db_name}"
        )


@lru_cache
def base_settings() -> CompanySettings:
    return CompanySettings()
