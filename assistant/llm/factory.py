"""``get_provider()``: pick the configured ``LLMProvider`` and always wrap it in the PII guard.

Where this sits: ``llm`` package; called once at start-up by ``api`` and by evals. It reads
``LLM_PROVIDER`` (``openai_agents | anthropic | scripted``) and builds the matching class.

Key ideas:
- Factory + lazy imports: a vendor module is imported only when selected, so a missing
  optional SDK cannot break the other providers.
- Always wrapped: callers never receive an unguarded provider (``MaskingProvider``).
- Importing this module is safe (no network, no credential checks); only ``get_provider()``
  can raise ``ProviderConfigError``.

Read next: ``llm/guard.py``, ``llm/openai_agents.py``.
See: docs/contracts.md Â§8.
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
