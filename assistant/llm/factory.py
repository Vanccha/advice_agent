"""`get_provider()` selects an `LLMProvider` implementation by `LLM_PROVIDER` (contracts
§8: `openai_agents | anthropic | scripted`) and always wraps it in the PII guard.

Import as: ``from llm.factory import get_provider``.

Importing this module is always safe (no network, no credential checks) — only calling
`get_provider()` for a live provider can raise `ProviderConfigError`, and only then.
"""
from __future__ import annotations

from core_common.settings import AssistantSettings, get_settings
from llm.base import LLMProvider, ProviderConfigError
from llm.guard import MaskingProvider


def get_provider(settings: AssistantSettings | None = None) -> LLMProvider:
    """Build the configured provider and wrap it in `MaskingProvider`. Callers never get
    an unwrapped provider from this factory."""
    settings = settings or get_settings()
    provider_name = settings.LLM_PROVIDER

    if provider_name == "scripted":
        from llm.scripted import ScriptedProvider

        inner: LLMProvider = ScriptedProvider()
    elif provider_name == "openai_agents":
        from llm.openai_agents import OpenAIAgentsProvider

        inner = OpenAIAgentsProvider(model=settings.LLM_MODEL, api_key=settings.OPENAI_API_KEY)
    elif provider_name == "anthropic":
        from llm.anthropic_provider import AnthropicProvider

        inner = AnthropicProvider(model=settings.ANTHROPIC_MODEL, api_key=settings.ANTHROPIC_API_KEY)
    else:
        raise ProviderConfigError(
            f"Unknown LLM_PROVIDER: {provider_name!r}. Expected one of "
            "'openai_agents', 'anthropic', 'scripted'."
        )

    return MaskingProvider(inner)
