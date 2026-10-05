"""FastAPI app (contracts §4.2): ``uvicorn api.main:app``.

Composes already-built modules — never reinvents them: `core_common` for config/db/
settings, `llm.factory`/`decision.factory` for the model/decision layer,
`mcp_gateway.gateway.ToolGateway` for the live tool surface, `audit.log.AuditLog`,
`observability.tracing.init_tracing`, and `modes.orchestrator.Orchestrator` as the single
place turn logic lives.

Startup degrades gracefully (contracts: a missing ``OPENAI_API_KEY`` must not crash the
container): if the database, tenant config or LLM provider cannot be built, ``/api/chat``
still answers — with a clear Turkish error message — instead of the process failing to
come up at all.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

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

ASSISTANT_DIR = Path(__file__).resolve().parent.parent  # assistant/
WEB_DIR = ASSISTANT_DIR / "web"

COOKIE_NAME = "demo_customer_no"
DEFAULT_CUSTOMER_PATTERN = r"^NH-\d{6}$"

app = FastAPI(title="NetHız destek asistanı API")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


class AppState:
    """Plain mutable bag of process-wide collaborators, built once at startup (and
    freely overridable by tests, which never run startup against a live stack)."""

    orchestrator: Orchestrator | None = None
    init_error_tr: str | None = None
    tenant_config: Any = None
    gateway: Any = None
    audit_log: AuditLog | None = None
    session_factory: Any = None


state = AppState()


@app.on_event("startup")
def _startup() -> None:
    settings = get_settings()
    init_tracing(settings)

    try:
        engine = get_engine()
        bootstrap_schema(engine)
        state.session_factory = get_sessionmaker()
    except Exception as exc:  # pragma: no cover - depends on live infra
        logger.warning("assistant database unavailable at startup: %s", exc)
        state.init_error_tr = "Asistan şu anda veritabanına bağlanamıyor, lütfen daha sonra tekrar deneyin."
        return

    try:
        tenant_config = load_tenant_config()
    except ConfigError as exc:
        logger.warning("tenant config error at startup: %s", exc)
        state.init_error_tr = "Asistan yapılandırması okunamadı, lütfen yöneticinize bildirin."
        return
    state.tenant_config = tenant_config
    state.audit_log = AuditLog(state.session_factory)

    try:
        provider = get_provider(settings)
    except ProviderConfigError as exc:
        logger.warning("LLM provider unavailable at startup: %s", exc)
        state.init_error_tr = (
            "Asistan şu anda dil modeli sağlayıcısına bağlanamadığı için yanıt veremiyor. "
            "Lütfen daha sonra tekrar deneyin."
        )
        return

    decision_service = get_decision_service(provider, settings, tenant_config)
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


def _degraded_reply(conversation_id: str | None) -> dict[str, Any]:
    return {
        "conversation_id": conversation_id or "unavailable",
        "mode": "CLOSING",
        "reply_tr": state.init_error_tr or "Asistan şu anda hizmet veremiyor, lütfen daha sonra tekrar deneyin.",
        "actions": [],
        "ticket_key": None,
        "requires_approval": False,
        "approval_id": None,
        "diagnosis": None,
    }


# --------------------------------------------------------------------------------------
# Request/response bodies
# --------------------------------------------------------------------------------------


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str
    customer_no: str | None = None


class LoginRequest(BaseModel):
    customer_no: str


class ApprovalRequest(BaseModel):
    decision: str


# --------------------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------------------


@app.get("/")
def index(request: Request):
    customer_no = request.cookies.get(COOKIE_NAME)
    return templates.TemplateResponse(request, "site.html", {"customer_no": customer_no})


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {})


# --------------------------------------------------------------------------------------
# Auth (demo-only, no password — contracts §4.2)
# --------------------------------------------------------------------------------------


@app.post("/api/login")
def api_login(body: LoginRequest):
    pattern = (
        state.tenant_config.tenant.customer_identifier.pattern
        if state.tenant_config is not None
        else DEFAULT_CUSTOMER_PATTERN
    )
    customer_no = body.customer_no.strip().upper()
    if not re.match(pattern, customer_no):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_FORMAT", "message": "Geçersiz müşteri numarası formatı"}},
        )

    if state.gateway is not None:
        outcome = state.gateway.call_sync("find_customer", {"customer_no": customer_no})
        if not getattr(outcome, "ok", False) or not outcome.data:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": "CUSTOMER_NOT_FOUND", "message": "Bilinmeyen müşteri numarası"}},
            )

    response = JSONResponse({"status": "ok", "customer_no": customer_no})
    response.set_cookie(COOKIE_NAME, customer_no, httponly=True, samesite="lax")
    return response


# --------------------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------------------


@app.post("/api/chat")
def api_chat(body: ChatRequest, request: Request):
    customer_no = body.customer_no or request.cookies.get(COOKIE_NAME)
    if state.orchestrator is None:
        return JSONResponse(_degraded_reply(body.conversation_id))
    result = state.orchestrator.handle_message(
        conversation_id=body.conversation_id, customer_no=customer_no, message=body.message
    )
    return JSONResponse(result.model_dump(mode="json"))


@app.get("/api/chat/stream")
async def api_chat_stream(request: Request, conversation_id: str | None = None, message: str = ""):
    customer_no = request.cookies.get(COOKIE_NAME)

    async def event_generator():
        if state.orchestrator is None:
            yield {"event": "error", "data": state.init_error_tr or "unavailable"}
            return
        try:
            result = state.orchestrator.handle_message(
                conversation_id=conversation_id, customer_no=customer_no, message=message
            )
        except Exception as exc:  # pragma: no cover - defensive, client falls back to POST
            logger.exception("chat/stream turn failed")
            yield {"event": "error", "data": str(exc)}
            return

        for word in result.reply_tr.split(" "):
            if word:
                yield {"event": "token", "data": word + " "}
        yield {"event": "final", "data": json.dumps(result.model_dump(mode="json"), ensure_ascii=False)}

    return EventSourceResponse(event_generator())


@app.post("/api/approvals/{approval_id}")
def api_approval(approval_id: str, body: ApprovalRequest):
    if body.decision not in ("granted", "denied"):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_DECISION", "message": "decision must be 'granted' or 'denied'"}},
        )
    if state.orchestrator is None:
        return JSONResponse(_degraded_reply(None))
    try:
        result = state.orchestrator.resolve_approval(
            approval_id=approval_id, granted=(body.decision == "granted")
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail={"error": {"code": "APPROVAL_NOT_FOUND", "message": str(exc)}}
        ) from exc
    return JSONResponse(result.model_dump(mode="json"))


@app.get("/api/conversations/{conversation_id}/audit")
def api_audit(conversation_id: str):
    if state.audit_log is None:
        return JSONResponse([])
    entries = state.audit_log.timeline(conversation_id)
    return JSONResponse(
        [
            {
                "step_type": entry.step_type,
                "summary": entry.summary,
                "reason": entry.reason,
                "created_at": entry.created_at.isoformat()
                if hasattr(entry.created_at, "isoformat")
                else str(entry.created_at),
            }
            for entry in entries
        ]
    )


# --------------------------------------------------------------------------------------
# Webhooks
# --------------------------------------------------------------------------------------


def _verify_webhook_signature(secret: str, body: bytes, signature: str | None) -> bool:
    """Same three lines as `company/shared/auth.py:verify_webhook_signature` —
    reimplemented, not imported (contracts hard rule: `assistant/` never imports
    `company`/`shared`)."""
    if not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/webhooks/ticket")
async def webhook_ticket(request: Request):
    settings = get_settings()
    raw_body = await request.body()
    signature = request.headers.get("X-Webhook-Signature")
    if not settings.WEBHOOK_SECRET or not _verify_webhook_signature(settings.WEBHOOK_SECRET, raw_body, signature):
        raise HTTPException(
            status_code=401, detail={"error": {"code": "INVALID_SIGNATURE", "message": "bad webhook signature"}}
        )
    try:
        event = json.loads(raw_body or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail={"error": {"code": "INVALID_BODY", "message": str(exc)}}) from exc

    if state.orchestrator is not None:
        state.orchestrator.handle_ticket_event(event)
    return {"status": "ok"}


@app.post("/webhooks/alert")
async def webhook_alert(request: Request):
    alert = await request.json()
    if state.orchestrator is not None:
        state.orchestrator.handle_alert(alert)
    return {"status": "ok"}


# --------------------------------------------------------------------------------------
# Operational
# --------------------------------------------------------------------------------------


@app.get("/health")
def health():
    return {"status": "ok", "service": "assistant", "version": "0.1.0"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
