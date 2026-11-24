"""Process-wide settings read from environment variables.

Where this sits: ``core_common``. Called by almost every package through ``get_settings()``.
It reads the environment only; it never touches tenant YAML (see ``config.py``).

Key ideas:
- Twelve-factor config: secrets and deployment switches (DB URL, API keys, provider choice)
  come from the environment, business rules come from tenant YAML.
- ``lru_cache`` makes settings a process-wide singleton; tests call ``get_settings.cache_clear()``.

Read next: ``core_common/config.py``.
See: docs/contracts.md §8.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class AssistantSettings(BaseSettings):
    """Every environment variable the assistant reads, with its default.

    Field names are the variable names (case-insensitive); unknown variables are ignored.
    """

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    # Tenant / config location
    TENANT: str = "netswift"
    TENANT_CONFIG_DIR: str = "/app/config/tenants"

    # Database
    ASSISTANT_DATABASE_URL: str | None = None

    # Model / decision layer (consumed by `llm` and `decision`)
    LLM_PROVIDER: str = "scripted"
    LLM_MODEL: str | None = None
    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL: str | None = None
    DECISION_SERVICE: str = "llm_structured"
    DECISION_MIN_CONFIDENCE: float = 0.6

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
