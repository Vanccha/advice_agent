"""Demo sign-in: `POST /api/login` sets the identity cookie.

Where this sits: HTTP layer. Called by `web/static/widget.js` and the login page; calls
the `find_customer` tool through `state.gateway`; produces the cookie that
`api/security.py` later reads as the caller's identity.

Key ideas:
- Validate the format first (cheap, tenant-configurable regex), then check existence.
- The cookie is `httponly` (page JavaScript cannot read it) and `samesite=lax`
  (not sent on cross-site POSTs), which limits theft and cross-site request forgery.

Demo simplification: there is no password. Anyone who knows a customer number can sign in
as that customer. Production would authenticate with the company's real identity provider
(OIDC/SSO) and issue a signed, expiring session, with `secure=True` on the cookie.

Read next: `api/security.py`.
"""
from __future__ import annotations

import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.errors import api_error
from api.security import COOKIE_NAME
from api.state import state
from modes.tool_data import first_record

router = APIRouter()

DEFAULT_CUSTOMER_PATTERN = r"^NS-\d{6}$"


class LoginRequest(BaseModel):
    customer_no: str


@router.post("/api/login")
def api_login(body: LoginRequest):
    """Set the identity cookie for a known customer number.

    200 ``{"status": "ok", "customer_no"}`` plus cookie; 400 INVALID_FORMAT when the
    number does not match the tenant pattern; 404 CUSTOMER_NOT_FOUND when the company
    system does not know it. With no gateway (degraded startup) the existence check is
    skipped so the demo page still works.
    """
    pattern = (
        state.tenant_config.tenant.customer_identifier.pattern
        if state.tenant_config is not None
        else DEFAULT_CUSTOMER_PATTERN
    )
    customer_no = body.customer_no.strip().upper()
    if not re.match(pattern, customer_no):
        raise api_error(400, "INVALID_FORMAT", "Invalid customer number format")

    if state.gateway is not None:
        outcome = state.gateway.call_sync("find_customer", {"customer_no": customer_no})
        if first_record(outcome) is None:
            raise api_error(404, "CUSTOMER_NOT_FOUND", "Unknown customer number")

    response = JSONResponse({"status": "ok", "customer_no": customer_no})
    response.set_cookie(COOKIE_NAME, customer_no, httponly=True, samesite="lax")
    return response
