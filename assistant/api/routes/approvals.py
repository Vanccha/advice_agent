"""Customer decisions on pending approvals: `POST /api/approvals/{approval_id}`.

Where this sits: HTTP layer. Called by the widget's approval card; delegates to
`state.orchestrator.resolve_approval`, which re-checks policy before executing anything.

Key ideas:
- Irreversible actions (CLAUDE.md rule 7) wait here for an explicit "granted" from the customer.
- Only the owner may answer; strangers and unknown ids get the same 404 (no id probing).

Read next: `api/security.py` (`require_approval_owner`), then `modes/orchestrator.py`.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.errors import api_error
from api.security import require_approval_owner, session_customer
from api.state import degraded_reply, state

router = APIRouter()


class ApprovalRequest(BaseModel):
    decision: str


@router.post("/api/approvals/{approval_id}")
def api_approval(approval_id: str, body: ApprovalRequest, request: Request):
    """Record the customer's decision and return the resulting turn.

    400 INVALID_DECISION unless decision is "granted" or "denied"; 404 APPROVAL_NOT_FOUND
    for unknown ids or approvals owned by someone else; degraded notice when startup failed.
    """
    if body.decision not in ("granted", "denied"):
        raise api_error(400, "INVALID_DECISION", "decision must be 'granted' or 'denied'")
    if state.orchestrator is None:
        return JSONResponse(degraded_reply(None))
    require_approval_owner(approval_id, session_customer(request))
    try:
        result = state.orchestrator.resolve_approval(
            approval_id=approval_id, granted=(body.decision == "granted")
        )
    except ValueError as exc:
        raise api_error(404, "APPROVAL_NOT_FOUND", str(exc)) from exc
    return JSONResponse(result.model_dump(mode="json"))
