"""Standalone UI preview harness for `assistant/web/`.

THIS IS NOT PART OF THE PRODUCT'S RUNTIME. The real assistant HTTP API (`assistant/api/`,
built separately — contracts §4.2) serves these same templates/static files for real, backed
by the policy engine, decision service, MCP tools and the Postgres-backed conversation state.

This file exists only so the templates/CSS/JS in this directory can be eyeballed and clicked
through without the rest of the stack running: it renders the Jinja templates with mock data
and answers the same four endpoints the widget calls with small canned/stubbed responses (no
LLM, no database, no company services). Nothing here is imported by, or shared with, the real
API — it is a developer tool, run directly:

    cd assistant/web && uvicorn preview:app --host 0.0.0.0 --port 8099

Run it from inside the test-runner container (see the service's DEBUG notes) so the installed
package set matches: `docker compose run --rm -p 8099:8099 -w /workspace/assistant/web
test-runner env PYTHONPATH=/workspace/assistant uvicorn preview:app --host 0.0.0.0 --port 8099`.
"""
from __future__ import annotations

import itertools
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="assistant/web preview harness (NOT the product API)")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# --------------------------------------------------------------------------------------
# Mock data — just enough to drive the widget visually.
# --------------------------------------------------------------------------------------

MOCK_CUSTOMERS = {
    "NH-100042": {"state": "active"},
    "NH-100017": {"state": "installation_scheduled"},
    "NH-100083": {"state": "awaiting_payment"},
    "NH-100005": {"state": "suspended"},
}

_conversation_counter = itertools.count(1)
_conversations: dict[str, dict[str, Any]] = {}

_MOCK_TURN_SEQUENCE = [
    {
        "mode": "ROUTER",
        "reply_tr": "Merhaba! Size nasıl yardımcı olabilirim? Paket önerisi, arıza teşhisi "
        "ya da mevcut talebinizin durumu hakkında yazabilirsiniz.",
        "actions": [],
        "ticket_key": None,
        "requires_approval": False,
        "approval_id": None,
    },
    {
        "mode": "DIAGNOSTIC",
        "reply_tr": "Aboneliğinizi kontrol ettim: kurulum işiniz OLT port yoğunluğu "
        "nedeniyle beklemede kalmış. Bu, sık karşılaşılan ve otomatik düzeltilebilen bir "
        "durum — kurulum işini şimdi yeniden kuyruğa almamıister misiniz?",
        "actions": [{"label_tr": "Teşhis: provisioning_status kontrol edildi"}],
        "ticket_key": None,
        "requires_approval": False,
        "approval_id": None,
    },
    {
        "mode": "ACTION",
        "reply_tr": "Bölgenizde devam eden bir arıza kaydı bulundu (INC-2026-014). Bu "
        "kesinti için hesabınıza 50 TL'ye kadar iyi niyet kredisi tanımlayabilirim, ancak bu "
        "geri alınamaz bir işlem olduğu için önce onayınızı almam gerekiyor.",
        "actions": [{"label_tr": "Politika kontrolü: apply_outage_credit"}],
        "ticket_key": None,
        "requires_approval": True,
        "approval_id": None,  # filled in per-conversation below
    },
    {
        "mode": "ESCALATED",
        "reply_tr": "Bu konuyu (çift tahsilat) doğrudan çözme yetkim yok; talebinizi "
        "Faturalama departmanına ilettim. Takip numaranız: TKT-2026-00031.",
        "actions": [{"label_tr": "Bilet oluşturuldu: BILLING"}],
        "ticket_key": "TKT-2026-00031",
        "requires_approval": False,
        "approval_id": None,
    },
]

_MOCK_AUDIT_STEPS = [
    {"step_type": "mode_decision", "summary": "Niyet 'problem_report' olarak sınıflandırıldı", "reason": "Anahtar kelime: 'kurulum', 'bekliyor'"},
    {"step_type": "tool_call", "summary": "diag.provisioning_status sorgulandı", "reason": "Teşhis kontrol listesi adım 4/7"},
    {"step_type": "policy_check", "summary": "retry_provisioning_job: izin verildi", "reason": "job.status=stuck, attempt_count<3"},
    {"step_type": "action", "summary": "Provisioning işi yeniden kuyruğa alındı", "reason": "Müşteri onayı gerekmiyor (politika)"},
]


def _mock_turn_for(conversation_id: str) -> dict[str, Any]:
    convo = _conversations.setdefault(conversation_id, {"turn": 0})
    index = min(convo["turn"], len(_MOCK_TURN_SEQUENCE) - 1)
    convo["turn"] += 1
    turn = dict(_MOCK_TURN_SEQUENCE[index])
    turn["conversation_id"] = conversation_id
    if turn["requires_approval"]:
        turn["approval_id"] = str(uuid.uuid4())
    return turn


# --------------------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------------------


@app.get("/")
def index(request: Request):
    customer_no = request.cookies.get("demo_customer_no", "NH-100042")
    return templates.TemplateResponse(request, "site.html", {"customer_no": customer_no})


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {})


@app.get("/widget-only")
def widget_only(request: Request):
    """Renders just the widget partial for quick isolated inspection."""
    return templates.TemplateResponse(request, "_widget.html", {"customer_no": "NH-100042"})


# --------------------------------------------------------------------------------------
# Stubbed chat endpoints — canned responses only, no LLM/DB/company calls.
# --------------------------------------------------------------------------------------


@app.post("/api/login")
async def api_login(request: Request):
    body = await request.json()
    customer_no = str(body.get("customer_no", "")).strip().upper()
    if customer_no not in MOCK_CUSTOMERS:
        return JSONResponse({"error": {"code": "CUSTOMER_NOT_FOUND", "message": "Bilinmeyen müşteri numarası"}}, status_code=404)
    response = JSONResponse({"status": "ok", "customer_no": customer_no})
    response.set_cookie("demo_customer_no", customer_no, httponly=True, samesite="lax")
    return response


@app.post("/api/chat")
async def api_chat(request: Request):
    body = await request.json()
    conversation_id = body.get("conversation_id") or f"conv-preview-{next(_conversation_counter)}"
    return JSONResponse(_mock_turn_for(conversation_id))


@app.get("/api/chat/stream")
async def api_chat_stream(request: Request, conversation_id: str | None = None, message: str = ""):
    conversation_id = conversation_id or f"conv-preview-{next(_conversation_counter)}"
    turn = _mock_turn_for(conversation_id)

    async def event_generator():
        words = turn["reply_tr"].split(" ")
        for word in words:
            yield {"event": "token", "data": word + " "}
        yield {"event": "final", "data": _json(turn)}

    return EventSourceResponse(event_generator())


@app.post("/api/approvals/{approval_id}")
async def api_approval(approval_id: str, request: Request):
    body = await request.json()
    decision = body.get("decision")
    reply = (
        "Teşekkürler, iyi niyet kredisini hesabınıza tanımladım."
        if decision == "granted"
        else "Anlaşıldı, krediyi uygulamadım."
    )
    return JSONResponse({
        "approval_id": approval_id,
        "decision": decision,
        "mode": "CLOSING",
        "reply_tr": reply,
        "ticket_key": None,
    })


@app.get("/api/conversations/{conversation_id}/audit")
async def api_audit(conversation_id: str):
    return JSONResponse(_MOCK_AUDIT_STEPS)


@app.get("/health")
def health():
    return {"status": "ok", "service": "assistant-web-preview", "version": "preview"}


def _json(obj: dict[str, Any]) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)
