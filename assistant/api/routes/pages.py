"""HTML pages: the demo host site (`/`) and the sign-in form (`/login`).

Where this sits: HTTP layer. Renders Jinja templates from `assistant/web/templates/`
via `api.templating`; reads the tenant config from `api.state`. Called by browsers.

Key ideas:
- Nothing customer-specific is hard-coded: the login form's label, placeholder and
  pattern come from the tenant's `customer_identifier` (CLAUDE.md hard rule 4).
- Pages only greet; the cookie shown here is never an authorisation decision.

Read next: `api/routes/auth.py`, then `web/templates/login.html`.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.security import COOKIE_NAME
from api.state import state
from api.templating import ASSET_VERSION, templates

router = APIRouter()

# Demo shortcuts, per tenant. A tenant with none falls back to the single documented
# example from its own `customer_identifier`, so the page is never customer-specific in code.
_DEMO_EXAMPLE_CUSTOMERS: dict[str, list[dict[str, str]]] = {
    "netswift": [
        {"customer_no": "NS-100042", "note_en": "active subscription"},
        {"customer_no": "NS-100017", "note_en": "awaiting installation"},
        {"customer_no": "NS-100083", "note_en": "payment pending"},
        {"customer_no": "NS-100005", "note_en": "suspended"},
    ],
}


@router.get("/")
def index(request: Request):
    """Demo host page with the embedded chat widget. Always 200; anonymous visitors allowed."""
    customer_no = request.cookies.get(COOKIE_NAME)
    return templates.TemplateResponse(
        request, "site.html", {"customer_no": customer_no, "asset_version": ASSET_VERSION}
    )


@router.get("/login")
def login_page(request: Request):
    """Sign-in form. Always 200; still renders with generic labels if tenant config failed to load."""
    identifier = (
        state.tenant_config.tenant.customer_identifier if state.tenant_config is not None else None
    )
    tenant_name = state.tenant_config.tenant_name if state.tenant_config is not None else ""
    examples = _DEMO_EXAMPLE_CUSTOMERS.get(tenant_name)
    if not examples and identifier is not None:
        examples = [{"customer_no": identifier.example, "note_en": "example"}]
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "identifier_label_en": identifier.label_en if identifier else "Customer number",
            "identifier_example": identifier.example if identifier else "",
            # The HTML `pattern` attribute is implicitly anchored, so the tenant's anchors
            # must come off or the field never validates.
            "identifier_html_pattern": (
                identifier.pattern.lstrip("^").rstrip("$") if identifier else ""
            ),
            "example_customers": examples or [],
            "asset_version": ASSET_VERSION,
        },
    )
