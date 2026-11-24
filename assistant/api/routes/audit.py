"""Audit trail for a conversation: `GET /api/conversations/{conversation_id}/audit`.

Where this sits: HTTP layer. Called by the widget's "why did the assistant do that" panel;
reads `state.audit_log` (hash-chained, append-only, see `audit/log.py`).

Key ideas:
- Transparency: customers can see the steps behind an answer, but only for their own
  conversation (same ownership rule as chat).

Read next: `audit/log.py`.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.security import require_conversation_access, session_customer
from api.state import state

router = APIRouter()


@router.get("/api/conversations/{conversation_id}/audit")
def api_audit(conversation_id: str, request: Request):
    """Return the conversation's audit steps as a JSON array (empty if audit is unavailable).

    403 FORBIDDEN when the conversation belongs to another customer.
    """
    require_conversation_access(conversation_id, session_customer(request))
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
