"""Chat turns: `POST /api/chat` (JSON) and `GET /api/chat/stream` (Server-Sent Events).

Where this sits: HTTP layer. Called by `web/static/widget.js`; delegates the whole turn to
`state.orchestrator.handle_message` (all conversation logic lives in `modes/`).

Key ideas:
- Identity only from the cookie (`api.security.session_customer`); a `customer_no` in the
  body is accepted for backward compatibility and ignored.
- Ownership is checked before the orchestrator runs, so a foreign conversation is never touched.
- Graceful degradation: with no orchestrator the endpoint still answers (a polite message).
- SSE (Server-Sent Events): a long-lived HTTP response of `event:`/`data:` frames.
  Simpler than WebSockets when data only flows server to browser.
- Blocking work (database, tool calls) is pushed to a thread pool so it does not freeze
  the async event loop that serves every other request.

Demo simplification: the stream is not real token streaming. The orchestrator finishes the
whole reply first and this route then splits it into words. Production would stream tokens
from the model as they are generated (which also complicates masking and policy checks).

Read next: `modes/orchestrator.py`.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from api.security import require_conversation_access, session_customer
from api.state import degraded_reply, state

logger = logging.getLogger("assistant.api")

router = APIRouter()


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str
    # Accepted for backward compatibility only and NEVER trusted: the customer identity
    # comes exclusively from the login cookie (see `session_customer`).
    customer_no: str | None = None


@router.post("/api/chat")
def api_chat(body: ChatRequest, request: Request):
    """Run one conversation turn and return its result as JSON.

    200 with the turn body (or a degraded notice when startup failed); 403 FORBIDDEN when
    the conversation is bound to another customer; 422 on a malformed body.
    """
    customer_no = session_customer(request)
    if state.orchestrator is None:
        return JSONResponse(degraded_reply(body.conversation_id))
    require_conversation_access(body.conversation_id, customer_no)
    result = state.orchestrator.handle_message(
        conversation_id=body.conversation_id, customer_no=customer_no, message=body.message
    )
    return JSONResponse(result.model_dump(mode="json"))


@router.get("/api/chat/stream")
async def api_chat_stream(request: Request, conversation_id: str | None = None, message: str = ""):
    """Run one turn and deliver it as SSE: `token` events, then one `final` event.

    Same ownership rule as `POST /api/chat` (403 before the stream starts). Failures after
    the stream starts arrive as an `error` event, because the 200 status is already sent;
    the widget reacts by retrying the turn with `POST /api/chat`.
    """
    customer_no = session_customer(request)
    if state.orchestrator is not None:
        await run_in_threadpool(require_conversation_access, conversation_id, customer_no)

    async def event_generator():
        if state.orchestrator is None:
            yield {"event": "error", "data": state.init_error_en or "unavailable"}
            return
        try:
            result = await run_in_threadpool(
                state.orchestrator.handle_message,
                conversation_id=conversation_id, customer_no=customer_no, message=message,
            )
        except Exception as exc:  # pragma: no cover - defensive, client falls back to POST
            logger.exception("chat/stream turn failed")
            yield {"event": "error", "data": str(exc)}
            return

        # Demo simplification: fake streaming of an already finished reply (see header).
        for word in result.reply_en.split(" "):
            if word:
                yield {"event": "token", "data": word + " "}
        yield {"event": "final", "data": json.dumps(result.model_dump(mode="json"), ensure_ascii=False)}

    return EventSourceResponse(event_generator())
