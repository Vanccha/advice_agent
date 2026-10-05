"""Adapter-wide settings, loaded from the environment (see docker-compose.yml's
`x-integration-env` anchor and docs/contracts.md §8).

Every `integrations/mcp_*` server imports `get_settings()` from here instead of
reading `os.environ` directly, so that adding a sixth company integration or
retargeting the product at a different tenant only means pointing these env
vars somewhere else.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class AdapterSettings(BaseSettings):
    """Env-driven configuration shared by every MCP server in this layer."""

    model_config = SettingsConfigDict(env_file=None, case_sensitive=True, extra="ignore")

    # Company service-account key used for every mutating REST call
    # (`partner-integration`, see contracts.md §2.1). Reads never use this key;
    # only `DIAG_DATABASE_URL`'s readonly_diag role does.
    COMPANY_API_KEY: str

    # Company REST bases (compose DNS names; see x-integration-env).
    CORE_API_BASE_URL: str = "http://core-api:8000"
    PAYMENT_API_BASE_URL: str = "http://payment-gateway:8000"
    TICKETING_API_BASE_URL: str = "http://ticketing:8000"
    NOTIFICATION_API_BASE_URL: str = "http://notification-hub:8000"
    PROMETHEUS_BASE_URL: str = "http://prometheus:9090"
    ALERTMANAGER_BASE_URL: str = "http://alertmanager:9093"

    # Where the monitoring adapter forwards a normalized alert. Only that adapter uses
    # it, but it belongs with the other deployment wiring rather than in os.environ.
    ASSISTANT_ALERT_WEBHOOK_URL: str = "http://assistant:8000/webhooks/alert"

    # readonly_diag DSN (SELECT-only on schema `diag`).
    DIAG_DATABASE_URL: str

    # Identity of this MCP server instance (also becomes the FastAPI/MCP server name).
    MCP_SERVER_NAME: str = "nethiz-mcp"

    LOG_LEVEL: str = "INFO"

    # Only used by servers built later on this same plumbing (mcp_payment,
    # mcp_ticketing, mcp_notification); optional so mcp_core doesn't need them.
    PSP_API_KEY: Optional[str] = None
    TICKETING_API_KEY: Optional[str] = None
    NOTIFY_API_KEY: Optional[str] = None


@lru_cache
def get_settings() -> AdapterSettings:
    """Process-wide cached settings instance."""
    return AdapterSettings()  # type: ignore[call-arg]  # values come from the environment
