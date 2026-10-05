"""Process-wide settings, read from the environment (contracts §8).

Import as: ``from core_common.settings import get_settings, AssistantSettings``.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class AssistantSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    # Tenant / config location
    TENANT: str = "nethiz"
    TENANT_CONFIG_DIR: str = "/app/config/tenants"

    # Database
    ASSISTANT_DATABASE_URL: str | None = None

    # Model / decision layer (consumed by assistant.llm / assistant.decision, not by us)
    LLM_PROVIDER: str = "scripted"
    LLM_MODEL: str | None = None
    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL: str | None = None
    DECISION_SERVICE: str = "llm_structured"
    DECISION_MIN_CONFIDENCE: float = 0.6
    MAX_TOOL_CALLS_PER_TURN: int = 8

    # MCP adapters
    MCP_CORE_URL: str | None = None
    MCP_PAYMENT_URL: str | None = None
    MCP_TICKETING_URL: str | None = None
    MCP_MONITORING_URL: str | None = None
    MCP_NOTIFICATION_URL: str | None = None

    # Webhooks
    WEBHOOK_SECRET: str | None = None

    # Observability
    LANGFUSE_ENABLED: bool = False
    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None
    LANGFUSE_BASE_URL: str | None = None

    # Evaluation / misc
    EVAL_MODE: str = "scripted"
    LOG_LEVEL: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> AssistantSettings:
    """Cached settings accessor. Call ``get_settings.cache_clear()`` in tests that patch env."""
    return AssistantSettings()
