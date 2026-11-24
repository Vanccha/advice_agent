"""Who is calling and what they may touch: cookie identity, ownership checks, webhook HMAC.

Where this sits: HTTP layer. Called by every router in `api/routes/`; reads `api.state`
and the assistant DB (`core_common.models`); raises errors built by `api/errors.py`.

Key ideas:
- Identity comes only from a server-set cookie. Request bodies and query strings are never
  trusted for "who am I", otherwise any client could claim to be any customer.
- Ownership checks: a conversation bound to a customer can only be continued by that
  customer. Approvals answer 404 (not 403) to strangers so ids cannot be probed.
- Webhooks are authenticated with an HMAC over the raw request bytes, compared in
  constant time (`hmac.compare_digest`) so response timing leaks nothing about the secret.

Read next: `api/routes/webhooks.py`, `api/routes/auth.py` (where the cookie is set).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import Request

from api.errors import api_error
from api.state import state
from core_common.db import session_scope
from core_common.models import Conversation, PendingApproval
from core_common.settings import get_settings

COOKIE_NAME = "demo_customer_no"


def session_customer(request: Request) -> str | None:
    """The only source of customer identity: the cookie `/api/login` set after its format
    and existence checks. Request bodies and query strings are never consulted."""
    return request.cookies.get(COOKIE_NAME) or None


def conversation_owner(conversation_id: str | None) -> tuple[bool, str | None]:
    """Return (exists, bound customer_no) for a conversation, read straight from the DB.

    The bound customer is None for anonymous conversations.
    """
    if not conversation_id or state.session_factory is None:
        return False, None
    with session_scope(state.session_factory) as session:
        row = session.query(Conversation).filter(Conversation.conversation_id == conversation_id).first()
        if row is None:
            return False, None
        return True, row.customer_no


def require_conversation_access(conversation_id: str | None, customer_no: str | None) -> None:
    """Raise 403 unless the conversation is new, anonymous, or bound to `customer_no`."""
    exists, owner = conversation_owner(conversation_id)
    if exists and owner is not None and owner != customer_no:
        raise api_error(403, "FORBIDDEN", "This conversation belongs to another customer")


def require_approval_owner(approval_id: str, customer_no: str | None) -> None:
    """The caller must be the customer the approval's conversation is bound to.

    Anything else answers 404 (not 403) so an approval id's existence is not revealed.
    Unknown or malformed ids fall through to the orchestrator, which answers 404 as well,
    so every "no such approval" case looks identical from outside.
    """
    try:
        approval_pk = int(approval_id)
    except (TypeError, ValueError):
        return
    if state.session_factory is None:
        return
    with session_scope(state.session_factory) as session:
        approval = session.get(PendingApproval, approval_pk)
        if approval is None:
            return
        conv_id = approval.conversation_id
    _, owner = conversation_owner(conv_id)
    if owner is None or owner != customer_no:
        raise api_error(404, "APPROVAL_NOT_FOUND", "unknown approval")


def verify_webhook_signature(secret: str, body: bytes, signature: str | None) -> bool:
    """Check ``X-Webhook-Signature: sha256=<hex hmac of the raw body>``.

    Reimplemented, not imported, from `company/shared/auth.py`: the boundary rule says
    `assistant/` never imports company code, so both sides agree on the algorithm instead.
    """
    if not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def verified_json(request: Request) -> Any:
    """Read the raw body, verify its signature and only then parse it as JSON.

    Raises 401 when WEBHOOK_SECRET is unset (fail closed: no secret means no way to
    authenticate, not "allow everything"), or the header is missing or wrong; 400 when the
    body is not JSON. The HMAC is computed over the exact bytes received, so the body must
    be read raw before any JSON parsing could re-serialise it differently.
    """
    settings = get_settings()
    raw_body = await request.body()
    signature = request.headers.get("X-Webhook-Signature")
    if not settings.WEBHOOK_SECRET or not verify_webhook_signature(settings.WEBHOOK_SECRET, raw_body, signature):
        raise api_error(401, "INVALID_SIGNATURE", "bad webhook signature")
    try:
        return json.loads(raw_body or b"{}")
    except json.JSONDecodeError as exc:
        raise api_error(400, "INVALID_BODY", str(exc)) from exc
