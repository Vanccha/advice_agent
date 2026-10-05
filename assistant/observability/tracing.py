"""Tracing wrapper around ``openinference-instrumentation-openai-agents`` + Langfuse SDK v3
(contracts §4.9, `docs/observability.md`).

``init_tracing()`` is a no-op when ``LANGFUSE_ENABLED`` is false, and **fail-open** when the
configured Langfuse host is unreachable or misconfigured: the problem is logged once at
WARNING and the process falls back to the local JSONL sink (`observability.fallback`) for the
rest of its life — it never raises, and a turn is never blocked or failed by an
observability problem.

Every attribute/metadata value that reaches Langfuse or the fallback sink has already gone
through ``observability.redaction`` (masked + leak-checked) — see that module's docstring for
why this wrapper never imports ``privacy.masking`` directly.

Usage (by `assistant/api` / `assistant/modes`, built in parallel):

    from observability.tracing import init_tracing, trace_turn, record_event, record_decision, record_tool_call

    handle = init_tracing(get_settings())   # once, at process startup

    with trace_turn(
        masked_customer_ref=masked_ref, conversation_id=conv_id, mode=mode.value,
        chaos_scenario=chaos_scenario, metadata={"tenant": tenant},
    ):
        ...                                  # record_event/_decision/_tool_call as you go
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from langfuse import Langfuse, get_client, propagate_attributes

from observability.fallback import append_fallback_record
from observability.redaction import redact_metadata, redact_payload

logger = logging.getLogger("assistant.observability.tracing")

#: Blocking network calls (Langfuse client construction's implicit health check is lazy, but
#: ``auth_check()`` is synchronous) are capped so a down Langfuse host cannot stall a demo.
_AUTH_CHECK_TIMEOUT_SECONDS = 5


@dataclass
class TracingHandle:
    """Returned by ``init_tracing()``; passed around (or fetched via
    ``get_tracing_handle()``) by every other helper in this module."""

    enabled: bool
    tenant: str
    client: Any | None = field(default=None, repr=False)
    _warned_runtime: bool = field(default=False, repr=False)

    def warn_once(self, message: str) -> None:
        if self._warned_runtime:
            return
        self._warned_runtime = True
        logger.warning(message)


_DISABLED_HANDLE = TracingHandle(enabled=False, tenant="nethiz", client=None)
_state_lock = threading.Lock()
_current_handle: TracingHandle = _DISABLED_HANDLE


def init_tracing(settings: Any) -> TracingHandle:
    """Set up tracing once at process startup. Always returns a usable handle; never raises.

    ``settings`` is an ``AssistantSettings`` instance (or anything with the same attributes):
    ``LANGFUSE_ENABLED``, ``LANGFUSE_PUBLIC_KEY``, ``LANGFUSE_SECRET_KEY``,
    ``LANGFUSE_BASE_URL``, ``TENANT``.
    """
    global _current_handle

    tenant = getattr(settings, "TENANT", "nethiz")
    enabled = bool(getattr(settings, "LANGFUSE_ENABLED", False))

    if not enabled:
        handle = TracingHandle(enabled=False, tenant=tenant, client=None)
        with _state_lock:
            _current_handle = handle
        return handle

    handle = _try_init_langfuse(settings, tenant)
    with _state_lock:
        _current_handle = handle
    return handle


def _try_init_langfuse(settings: Any, tenant: str) -> TracingHandle:
    public_key = getattr(settings, "LANGFUSE_PUBLIC_KEY", None)
    secret_key = getattr(settings, "LANGFUSE_SECRET_KEY", None)
    base_url = getattr(settings, "LANGFUSE_BASE_URL", None)

    try:
        if not public_key or not secret_key or not base_url:
            raise RuntimeError(
                "LANGFUSE_ENABLED is true but LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY/"
                "LANGFUSE_BASE_URL are not fully configured"
            )

        # Registers this (public_key, secret_key, host) as the process's Langfuse instance;
        # get_client(public_key=...) below returns the same bound client (see langfuse's
        # LangfuseResourceManager — the reason we pass public_key explicitly rather than
        # relying on env-var auto-discovery, which this repo's settings names don't match).
        Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=base_url,
            timeout=_AUTH_CHECK_TIMEOUT_SECONDS,
        )
        client = get_client(public_key=public_key)

        if not client.auth_check():
            raise RuntimeError("Langfuse auth_check() returned False for the configured keys")

        _instrument_openai_agents()

        logger.info("Langfuse tracing enabled for tenant=%s host=%s", tenant, base_url)
        return TracingHandle(enabled=True, tenant=tenant, client=client)

    except Exception as exc:
        logger.warning(
            "Langfuse unreachable or misconfigured (%s); falling back to local JSONL trace "
            "sink for tenant=%s. The conversation is unaffected.",
            exc,
            tenant,
        )
        return TracingHandle(enabled=False, tenant=tenant, client=None)


def _instrument_openai_agents() -> None:
    """Best-effort: a failure here must not take down tracing entirely (the Langfuse side
    of the handle may still be healthy even if this particular instrumentation isn't)."""
    try:
        from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

        OpenAIAgentsInstrumentor().instrument()
    except Exception:
        logger.warning(
            "openinference-instrumentation-openai-agents could not be enabled; agent/tool "
            "spans will not be auto-captured, but explicit record_* calls still work",
            exc_info=True,
        )


def get_tracing_handle() -> TracingHandle:
    """The handle set by the last ``init_tracing()`` call, or a disabled handle if that was
    never called (e.g. a unit test importing this module directly)."""
    with _state_lock:
        return _current_handle


def reset_tracing_for_tests() -> None:
    """Test helper: restore the disabled handle between tests."""
    global _current_handle
    with _state_lock:
        _current_handle = _DISABLED_HANDLE


# --------------------------------------------------------------------------------------
# Per-turn context
# --------------------------------------------------------------------------------------


@contextmanager
def trace_turn(
    *,
    masked_customer_ref: str,
    conversation_id: str,
    mode: str,
    chaos_scenario: str | None = None,
    metadata: dict[str, Any] | None = None,
    tenant: str | None = None,
) -> Iterator[None]:
    """Wrap one conversation turn (contracts §4.9).

    ``masked_customer_ref`` must already be masked by the caller (e.g.
    ``mask_payload({"customer_no": ...})`` upstream) — this function redacts ``metadata``
    for you but treats ``masked_customer_ref``/``conversation_id``/``mode``/``chaos_scenario``
    as identifiers, not free text, so it does not re-mask them.
    """
    handle = get_tracing_handle()
    effective_tenant = tenant or handle.tenant
    # Four filterable tags, as the demo needs: tenant, masked customer reference, mode and
    # chaos scenario. The masked reference is also set as Langfuse's own user_id, but a tag
    # is what makes it filterable alongside the others in one query.
    tags = [effective_tenant, masked_customer_ref or "anonymous", mode, chaos_scenario or "none"]
    safe_metadata = redact_metadata(metadata)

    fallback_record = {
        "kind": "turn",
        "tenant": effective_tenant,
        "user_id": masked_customer_ref,
        "session_id": conversation_id,
        "tags": tags,
        "metadata": safe_metadata,
    }

    if not handle.enabled or handle.client is None:
        append_fallback_record(fallback_record)
        yield
        return

    # propagate_attributes() is documented to never raise (invalid values are logged and
    # dropped by the SDK itself) — see langfuse's own docstring — so no try/except is needed
    # around the turn body here. We still mirror to the fallback sink so a demo has the
    # reasoning trail available locally even when Langfuse is also receiving it.
    append_fallback_record(fallback_record)
    with propagate_attributes(
        user_id=masked_customer_ref,
        session_id=conversation_id,
        tags=tags,
        metadata=safe_metadata,
    ):
        yield


# --------------------------------------------------------------------------------------
# Span-level helpers
# --------------------------------------------------------------------------------------


def _emit(name: str, *, input: Any = None, output: Any = None) -> None:
    """Shared emit path for the record_* helpers below. Never raises."""
    handle = get_tracing_handle()
    record = {"kind": "event", "name": name, "input": input, "output": output}

    if handle.enabled and handle.client is not None:
        try:
            handle.client.create_event(name=name, input=input, output=output)
            return
        except Exception:
            handle.warn_once(
                f"Langfuse create_event failed for '{name}'; writing to the local JSONL "
                "fallback sink instead for the rest of this process"
            )

    append_fallback_record(record)


def record_event(name: str, payload: dict[str, Any] | None = None) -> None:
    """Record an arbitrary named step (e.g. ``mode_decision``, ``policy_check``)."""
    try:
        safe_payload = redact_metadata(payload)
        _emit(name, output=safe_payload)
    except Exception:  # pragma: no cover - defensive, must never raise
        logger.warning("record_event('%s') failed unexpectedly", name, exc_info=True)


def record_decision(
    decision_name: str,
    *,
    value: Any,
    confidence: float,
    rationale: str,
    model: str,
) -> None:
    """Record one ``DecisionService`` call (contracts §4.5)."""
    try:
        safe = redact_payload(
            {"value": value, "confidence": confidence, "rationale": rationale, "model": model}
        )
        _emit(f"decision:{decision_name}", output=safe)
    except Exception:  # pragma: no cover - defensive, must never raise
        logger.warning("record_decision('%s') failed unexpectedly", decision_name, exc_info=True)


def record_tool_call(
    tool_name: str,
    *,
    tool_input: dict[str, Any] | None = None,
    tool_output: Any = None,
) -> None:
    """Record one MCP tool invocation."""
    try:
        safe_input = redact_metadata(tool_input)
        safe_output = redact_payload(tool_output) if tool_output is not None else None
        _emit(f"tool:{tool_name}", input=safe_input, output=safe_output)
    except Exception:  # pragma: no cover - defensive, must never raise
        logger.warning("record_tool_call('%s') failed unexpectedly", tool_name, exc_info=True)
