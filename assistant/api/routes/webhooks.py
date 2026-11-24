"""Inbound webhooks from the company side: ticket status changes and monitoring alerts.

Where this sits: HTTP layer. Called by the ticketing service and by `mcp-monitoring`
(machine to machine, no cookie); hands the event to the orchestrator.

Key ideas:
- Authenticity: every call must carry a valid HMAC signature (`api.security.verified_json`).
- Acknowledge fast: the handler returns ``{"status": "ok"}`` once the event is processed,
  so senders do not retry endlessly. A missing orchestrator (degraded startup) still acks.
- The orchestrator is synchronous, so it runs in a thread pool to keep the event loop free.

Read next: `api/security.py`.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from api.security import verified_json
from api.state import state

router = APIRouter()


@router.post("/webhooks/ticket")
async def webhook_ticket(request: Request):
    """Receive a `ticket.status_changed` event. 401 on a bad signature, 400 on non-JSON."""
    event = await verified_json(request)
    if state.orchestrator is not None:
        await run_in_threadpool(state.orchestrator.handle_ticket_event, event)
    return {"status": "ok"}


@router.post("/webhooks/alert")
async def webhook_alert(request: Request):
    """Receive a normalised monitoring alert (proactive diagnosis). Same errors as the ticket hook."""
    alert = await verified_json(request)
    if state.orchestrator is not None:
        await run_in_threadpool(state.orchestrator.handle_alert, alert)
    return {"status": "ok"}
