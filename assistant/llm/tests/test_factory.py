import pytest

from core_common.settings import AssistantSettings
from llm.base import ProviderConfigError
from llm.factory import get_provider
from llm.guard import MaskingProvider
from llm.scripted import ScriptedProvider


def test_scripted_provider_needs_no_credentials():
    settings = AssistantSettings(LLM_PROVIDER="scripted")
    provider = get_provider(settings)
    assert isinstance(provider, MaskingProvider)
    assert provider.name == "scripted"


def test_openai_agents_without_a_key_fails_loudly_not_at_import_time():
    # Importing the module (already done transitively above) must not have raised.
    settings = AssistantSettings(LLM_PROVIDER="openai_agents", OPENAI_API_KEY=None)
    with pytest.raises(ProviderConfigError):
        get_provider(settings)


def test_anthropic_without_a_key_fails_loudly():
    settings = AssistantSettings(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="")
    with pytest.raises(ProviderConfigError):
        get_provider(settings)


def test_unknown_provider_name_fails_loudly():
    settings = AssistantSettings(LLM_PROVIDER="not_a_real_provider")
    with pytest.raises(ProviderConfigError):
        get_provider(settings)
