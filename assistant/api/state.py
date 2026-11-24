"""Process-wide collaborators (`AppState`) and the startup wiring that builds them.

Where this sits: this file is the application's composition root. `api.main` calls
`startup()` from the FastAPI lifespan; every router reads the module-level `state`.
It calls `core_common` (config/db/settings), `llm.factory`, `decision.factory`,
`mcp_gateway.gateway.ToolGateway`, `audit.log.AuditLog`, `observability.tracing` and
`modes.orchestrator.Orchestrator`.

Key ideas:
- Composition root: the one place where concrete objects are created and connected.
  Everything else receives collaborators ready-made, so swapping a provider or gateway
  never touches route code, and tests can replace pieces by assigning to `state`.
- Graceful degradation: if the database, tenant config or LLM provider cannot be built,
  `startup()` records a customer-facing `init_error_en` and returns instead of crashing.
  The container still comes up, `/health` stays green and `/api/chat` answers with a clear
  message. Rule: a missing optional dependency must not stop the process from starting.
- Simplification for teaching: `state` is a mutable module-level singleton, which is
  easy to read and to override in tests. Larger apps usually prefer FastAPI dependency
  injection (`Depends`) with objects stored on `app.state`.

Read next: `api/routes/chat.py` (the main consumer of `state.orchestrator`).
"""
from __future__ import annotations

import logging
from typing import Any

from audit.log import AuditLog
from core_common.config import ConfigError, load_tenant_config
from core_common.db import bootstrap_schema, get_engine, get_sessionmaker
from core_common.settings import get_settings
from decision.factory import get_decision_service
from llm.base import ProviderConfigError
from llm.factory import get_provider
from mcp_gateway.gateway import ToolGateway
from modes.orchestrator import Orchestrator
from observability.tracing import init_tracing

logger = logging.getLogger("assistant.api")


class AppState:
    """Plain mutable bag of process-wide collaborators, built once at startup.

    Every field starts as None and is filled by `startup()` in dependency order, so a
    partially filled state is a meaningful "degraded" state: `orchestrator is None` means
    "chat is unavailable", and `init_error_en` says why. Tests overwrite fields directly
    because they never run startup against a live stack.
    """

    orchestrator: Orchestrator | None = None
    init_error_en: str | None = None
    tenant_config: Any = None
    gateway: Any = None
    audit_log: AuditLog | None = None
    session_factory: Any = None


state = AppState()


def startup() -> None:
    """Build the collaborators in order, stopping (degraded, not crashed) at the first failure."""
    settings = get_settings()
    init_tracing(settings)

    try:
        engine = get_engine()
        bootstrap_schema(engine)
        state.session_factory = get_sessionmaker()
    except Exception as exc:  # pragma: no cover - depends on live infra
        logger.warning("assistant database unavailable at startup: %s", exc)
        state.init_error_en = "The assistant cannot reach its database right now; please try again later."
        return

    try:
        tenant_config = load_tenant_config()
    except ConfigError as exc:
        logger.warning("tenant config error at startup: %s", exc)
        state.init_error_en = "The assistant configuration could not be read; please let your administrator know."
        return
    state.tenant_config = tenant_config
    state.audit_log = AuditLog(state.session_factory)

    try:
        provider = get_provider(settings)
    except ProviderConfigError as exc:
        logger.warning("LLM provider unavailable at startup: %s", exc)
        state.init_error_en = (
            "The assistant cannot reply right now because it cannot reach its language model "
            "provider. Please try again later."
        )
        return

    decision_service = get_decision_service(provider, settings, tenant_config)
    # This gateway is the shared root; the orchestrator derives a per-turn copy from it
    # (`for_turn`), so "startup" only labels calls made outside any conversation.
    gateway = ToolGateway.from_tenant_config(
        tenant_config, state.audit_log, conversation_id="startup", actor="assistant"
    )
    state.gateway = gateway
    state.orchestrator = Orchestrator(
        tenant_config=tenant_config,
        settings=settings,
        provider=provider,
        decision_service=decision_service,
        gateway=gateway,
        audit_log=state.audit_log,
        session_factory=state.session_factory,
    )


def degraded_reply(conversation_id: str | None) -> dict[str, Any]:
    """A well-formed chat body explaining that the assistant is unavailable.

    It has the same shape as a real turn so the widget renders it like any reply.
    """
    return {
        "conversation_id": conversation_id or "unavailable",
        "mode": "CLOSING",
        "reply_en": state.init_error_en or "The assistant is unavailable right now; please try again later.",
        "actions": [],
        "ticket_key": None,
        "requires_approval": False,
        "approval_id": None,
        "diagnosis": None,
    }
