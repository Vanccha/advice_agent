import pytest
from pydantic import BaseModel

from llm.base import ChatMessage, ProviderHealth
from llm.guard import MaskingProvider, PIILeakError


class _Echo(BaseModel):
    text: str


class _RecordingProvider:
    """A minimal fake `LLMProvider` that records whether it was ever called, so tests can
    prove the guard short-circuits *before* reaching the inner provider."""

    name = "recording"
    model = "recording-model"

    def __init__(self) -> None:
        self.called = False

    def complete(self, *, system, messages, temperature=0.2, max_tokens=None) -> str:
        self.called = True
        return "ok"

    def structured(self, *, system, messages, schema, temperature=0.0):
        self.called = True
        return schema(text="ok")

    def health(self) -> ProviderHealth:
        return ProviderHealth(ok=True, provider=self.name, model=self.model)


def test_complete_blocks_a_raw_phone_number_and_never_calls_the_inner_provider():
    inner = _RecordingProvider()
    guarded = MaskingProvider(inner)
    messages = [ChatMessage(role="user", content="Please call me on 07700 900 131")]
    with pytest.raises(PIILeakError):
        guarded.complete(system="sys", messages=messages)
    assert inner.called is False


def test_complete_blocks_a_raw_national_id():
    inner = _RecordingProvider()
    guarded = MaskingProvider(inner)
    messages = [ChatMessage(role="user", content="NI number QQ123456C")]
    with pytest.raises(PIILeakError):
        guarded.complete(system="sys", messages=messages)
    assert inner.called is False


def test_structured_also_blocks_raw_pii_in_the_system_prompt():
    inner = _RecordingProvider()
    guarded = MaskingProvider(inner)
    system = "Customer phone is 07700 900 131, use it to verify identity."
    with pytest.raises(PIILeakError):
        guarded.structured(system=system, messages=[ChatMessage(role="user", content="hi")], schema=_Echo)
    assert inner.called is False


def test_clean_text_passes_through_to_the_inner_provider():
    inner = _RecordingProvider()
    guarded = MaskingProvider(inner)
    reply = guarded.complete(system="sys", messages=[ChatMessage(role="user", content="hello")])
    assert reply == "ok"
    assert inner.called is True


def test_guard_exposes_inner_name_and_model():
    inner = _RecordingProvider()
    guarded = MaskingProvider(inner)
    assert guarded.name == "recording"
    assert guarded.model == "recording-model"
