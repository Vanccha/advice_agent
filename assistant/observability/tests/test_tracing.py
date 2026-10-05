from __future__ import annotations

import json
import logging

import pytest

from observability import fallback, tracing
from observability.tests.conftest import make_settings


class _BoomLangfuse:
    def __init__(self, *args, **kwargs):  # pragma: no cover - should never be constructed
        raise AssertionError("Langfuse() must not be constructed when LANGFUSE_ENABLED is false")


def test_disabled_makes_no_network_attempt_and_does_not_raise(monkeypatch):
    monkeypatch.setattr(tracing, "Langfuse", _BoomLangfuse)
    monkeypatch.setattr(
        tracing,
        "get_client",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("get_client must not be called")),
    )

    settings = make_settings(LANGFUSE_ENABLED=False)
    handle = tracing.init_tracing(settings)

    assert handle.enabled is False
    assert handle.client is None

    # trace_turn must also work fully offline, writing only to the fallback sink.
    with tracing.trace_turn(
        masked_customer_ref="NH-1****2", conversation_id="conv-1", mode="ADVISORY"
    ):
        pass


class _FakeClientAuthFails:
    def auth_check(self) -> bool:
        raise ConnectionError("simulated: Langfuse host unreachable")


def test_unreachable_host_warns_once_never_raises_and_falls_back_to_jsonl(
    monkeypatch, tmp_path, caplog
):
    monkeypatch.setattr(tracing, "Langfuse", lambda **kwargs: None)
    monkeypatch.setattr(tracing, "get_client", lambda **kwargs: _FakeClientAuthFails())
    monkeypatch.setattr(tracing, "_instrument_openai_agents", lambda: None)
    monkeypatch.setattr(fallback, "DEFAULT_TRACE_DIR", tmp_path)

    settings = make_settings(
        LANGFUSE_ENABLED=True,
        LANGFUSE_PUBLIC_KEY="pk",
        LANGFUSE_SECRET_KEY="sk",
        LANGFUSE_BASE_URL="http://unreachable.invalid:3000",
    )

    with caplog.at_level(logging.WARNING, logger="assistant.observability.tracing"):
        handle = tracing.init_tracing(settings)

    assert handle.enabled is False
    assert handle.client is None
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name == "assistant.observability.tracing"
    ]
    assert len(warnings) == 1
    assert "unreachable" in warnings[0].getMessage()

    with tracing.trace_turn(
        masked_customer_ref="NH-1****2",
        conversation_id="conv-42",
        mode="DIAGNOSTIC",
        chaos_scenario="stuck_provisioning",
    ):
        tracing.record_event("mode_decision", {"note": "ok"})

    written = list(tmp_path.glob("*.jsonl"))
    assert len(written) == 1
    lines = written[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # one "turn" record + one "event" record
    turn_record = json.loads(lines[0])
    assert turn_record["session_id"] == "conv-42"
    assert turn_record["tags"] == ["nethiz", "DIAGNOSTIC", "stuck_provisioning"]


class _FakeClientHealthy:
    def __init__(self):
        self.events: list[dict] = []

    def auth_check(self) -> bool:
        return True

    def create_event(self, *, name, input=None, output=None):
        self.events.append({"name": name, "input": input, "output": output})


def test_four_filterable_dimensions_are_attached_to_propagate_attributes(monkeypatch):
    fake_client = _FakeClientHealthy()
    monkeypatch.setattr(tracing, "Langfuse", lambda **kwargs: None)
    monkeypatch.setattr(tracing, "get_client", lambda **kwargs: fake_client)
    monkeypatch.setattr(tracing, "_instrument_openai_agents", lambda: None)

    captured = {}

    from contextlib import contextmanager

    @contextmanager
    def fake_propagate_attributes(**kwargs):
        captured.update(kwargs)
        yield

    monkeypatch.setattr(tracing, "propagate_attributes", fake_propagate_attributes)

    settings = make_settings(
        LANGFUSE_ENABLED=True,
        LANGFUSE_PUBLIC_KEY="pk",
        LANGFUSE_SECRET_KEY="sk",
        LANGFUSE_BASE_URL="http://langfuse-web:3000",
    )
    handle = tracing.init_tracing(settings)
    assert handle.enabled is True

    with tracing.trace_turn(
        masked_customer_ref="NH-1****2",
        conversation_id="conv-7",
        mode="ACTION",
        chaos_scenario=None,
    ):
        pass

    assert captured["user_id"] == "NH-1****2"
    assert captured["session_id"] == "conv-7"
    # tenant, mode, chaos_scenario-or-"none": exactly the three contracts §4.9 tags, plus
    # the masked customer ref carried separately as user_id (the fourth filterable
    # dimension docs/observability.md describes).
    assert captured["tags"] == ["nethiz", "ACTION", "none"]


def test_payload_containing_raw_pii_is_redacted_before_it_reaches_the_emitter(monkeypatch):
    fake_client = _FakeClientHealthy()
    monkeypatch.setattr(tracing, "Langfuse", lambda **kwargs: None)
    monkeypatch.setattr(tracing, "get_client", lambda **kwargs: fake_client)
    monkeypatch.setattr(tracing, "_instrument_openai_agents", lambda: None)

    settings = make_settings(
        LANGFUSE_ENABLED=True,
        LANGFUSE_PUBLIC_KEY="pk",
        LANGFUSE_SECRET_KEY="sk",
        LANGFUSE_BASE_URL="http://langfuse-web:3000",
    )
    tracing.init_tracing(settings)

    tracing.record_tool_call(
        "get_customer_overview",
        tool_input={"customer_no": "NH-100042"},
        tool_output={
            "full_name": "Ahmet Yilmaz",
            "national_id": "12345678901",
            "phone": "+905551234567",
            "email": "ahmet.yilmaz@ornek-eposta.test",
        },
    )

    assert len(fake_client.events) == 1
    emitted = json.dumps(fake_client.events[0], ensure_ascii=False)
    assert "12345678901" not in emitted
    assert "+905551234567" not in emitted
    assert "ahmet.yilmaz@ornek-eposta.test" not in emitted
    assert "Ahmet Yilmaz" not in emitted


@pytest.mark.parametrize(
    "bad_payload",
    [
        None,
        object(),
        {"circular": None},
        {"nested": {"deep": [1, 2, {"x": object()}]}},
    ],
)
def test_record_helpers_never_raise_on_bad_input(monkeypatch, bad_payload):
    class _ExplodingClient:
        def auth_check(self):
            return True

        def create_event(self, **kwargs):
            raise RuntimeError("simulated Langfuse transport failure")

    monkeypatch.setattr(tracing, "Langfuse", lambda **kwargs: None)
    monkeypatch.setattr(tracing, "get_client", lambda **kwargs: _ExplodingClient())
    monkeypatch.setattr(tracing, "_instrument_openai_agents", lambda: None)

    settings = make_settings(
        LANGFUSE_ENABLED=True,
        LANGFUSE_PUBLIC_KEY="pk",
        LANGFUSE_SECRET_KEY="sk",
        LANGFUSE_BASE_URL="http://langfuse-web:3000",
    )
    tracing.init_tracing(settings)

    # None of these may raise, regardless of how malformed the payload is or whether the
    # (fake) Langfuse transport itself explodes.
    tracing.record_event("weird_event", bad_payload if isinstance(bad_payload, dict) else {"v": bad_payload})
    tracing.record_decision(
        "classify_intent", value=bad_payload, confidence=0.9, rationale="x", model="scripted"
    )
    tracing.record_tool_call("some_tool", tool_input=None, tool_output=bad_payload)
