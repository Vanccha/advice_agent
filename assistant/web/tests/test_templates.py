"""Template-level tests for assistant/web (no HTTP server involved — these render the Jinja
templates directly). Covers: user-facing labels, the element ids/data attributes the
widget's own JS (and this project's e2e checks) depend on, and the hard rule that nothing
under assistant/ hardcodes a company/backend hostname — the widget must only use relative
URLs (CLAUDE.md rule 4 / contracts.md boundary tests)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

WEB_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

_ABSOLUTE_URL_ATTR_RE = re.compile(r'(?:href|src|action)\s*=\s*["\']https?://', re.IGNORECASE)
_ABSOLUTE_URL_JS_RE = re.compile(r'(?:fetch|EventSource)\s*\(\s*["\']https?://', re.IGNORECASE)
_SUSPICIOUS_HOST_RE = re.compile(r'localhost|127\.0\.0\.1|assistant:8000|netswift\.com', re.IGNORECASE)


@pytest.fixture(scope="module")
def jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def render(jinja_env: Environment, name: str, **context) -> str:
    return jinja_env.get_template(name).render(**context)


# --------------------------------------------------------------------------------------
# login.html
# --------------------------------------------------------------------------------------


# The login form belongs to the product, not to one customer, so every customer-specific
# string on it arrives as context. These fixtures stand in for two different tenant files.
NETSWIFT_LOGIN_CONTEXT = {
    "identifier_label_en": "Customer number",
    "identifier_example": "NS-100042",
    "identifier_html_pattern": r"NS-\d{6}",
    "example_customers": [
        {"customer_no": "NS-100042", "note_en": "active subscription"},
        {"customer_no": "NS-100017", "note_en": "awaiting installation"},
        {"customer_no": "NS-100083", "note_en": "payment pending"},
    ],
}

OTHER_TENANT_LOGIN_CONTEXT = {
    "identifier_label_en": "Account number",
    "identifier_example": "OR-2045118",
    "identifier_html_pattern": "OR-[0-9]{7}",
    "example_customers": [{"customer_no": "OR-2045118", "note_en": "example"}],
}


def test_login_page_has_labels_and_demo_disclaimer(jinja_env):
    html = render(jinja_env, "login.html", **NETSWIFT_LOGIN_CONTEXT)

    assert "Customer number" in html
    assert "demo" in html.lower()
    assert 'data-testid="demo-login-note"' in html
    # no password field anywhere — this is explicitly a password-less demo login
    assert 'type="password"' not in html


def test_login_page_has_required_ids_and_example_customers(jinja_env):
    html = render(jinja_env, "login.html", **NETSWIFT_LOGIN_CONTEXT)

    assert 'id="login-form"' in html
    assert 'data-testid="login-form"' in html
    assert 'id="customer_no"' in html
    assert 'name="customer_no"' in html
    assert 'data-testid="customer-no-input"' in html
    assert 'data-testid="login-error"' in html
    assert 'data-testid="login-submit"' in html

    assert 'data-testid="example-customers"' in html
    example_customer_nos = re.findall(r'data-customer-no="(NS-\d{6})"', html)
    assert len(example_customer_nos) >= 3
    assert "NS-100042" in example_customer_nos


def test_login_page_is_tenant_driven_not_hardcoded(jinja_env):
    """Rendered for a different tenant, the page must carry that tenant's identifier —
    label, placeholder, input pattern and demo shortcuts — and none of NetSwift's."""
    html = render(jinja_env, "login.html", **OTHER_TENANT_LOGIN_CONTEXT)

    assert "Account number" in html
    assert "Customer number" not in html
    assert 'placeholder="OR-2045118"' in html
    assert 'pattern="OR-[0-9]{7}"' in html
    assert 'data-customer-no="OR-2045118"' in html
    assert "NS-" not in html


# --------------------------------------------------------------------------------------
# _widget.html
# --------------------------------------------------------------------------------------


def test_widget_partial_has_required_ids_and_data_testids(jinja_env):
    html = render(jinja_env, "_widget.html", customer_no=None)

    required_ids = [
        "chat-widget",
        "chat-launcher",
        "chat-panel",
        "mode-badge",
        "connection-status",
        "chat-messages",
        "typing-indicator",
        "chat-error",
        "audit-panel",
        "audit-steps",
        "chat-form",
        "chat-input",
        "chat-send",
        "chat-minimize",
    ]
    for element_id in required_ids:
        assert f'id="{element_id}"' in html, f"missing id={element_id!r}"

    required_testids = [
        "chat-widget",
        "chat-launcher",
        "chat-panel",
        "mode-badge",
        "chat-messages",
        "typing-indicator",
        "chat-error",
        "audit-panel",
        "audit-steps",
        "chat-form",
        "chat-input",
        "chat-send",
        "approval-card",
        "approval-approve",
        "approval-deny",
        "ticket-chip",
    ]
    for testid in required_testids:
        assert f'data-testid="{testid}"' in html, f"missing data-testid={testid!r}"


def test_widget_mode_badge_defaults_to_router(jinja_env):
    html = render(jinja_env, "_widget.html", customer_no=None)
    assert 'data-mode="ROUTER"' in html
    assert "Routing" in html


def test_widget_renders_user_facing_copy(jinja_env):
    html = render(jinja_env, "_widget.html", customer_no=None)
    assert "Type your message" in html
    assert "What did the assistant do?" in html
    assert "Your approval is needed" in html
    assert "Approve" in html
    assert "Decline" in html


def test_widget_session_chip_only_rendered_when_customer_no_given(jinja_env):
    with_customer = render(jinja_env, "_widget.html", customer_no="NS-100042")
    without_customer = render(jinja_env, "_widget.html", customer_no=None)

    assert 'data-testid="session-chip"' in with_customer
    assert "NS-100042" in with_customer
    assert 'data-testid="session-chip"' not in without_customer


# --------------------------------------------------------------------------------------
# site.html
# --------------------------------------------------------------------------------------


def test_site_page_embeds_the_widget_and_is_visually_a_guest(jinja_env):
    html = render(jinja_env, "site.html", customer_no=None)

    # it is obviously NetSwift's own page...
    assert "NetSwift" in html
    assert "Fibre" in html

    # ...with the widget included as a distinguishable, separately-styled guest.
    assert 'id="chat-widget"' in html
    assert 'id="chat-launcher"' in html
    assert "/static/widget.css" in html
    assert "/static/site.css" in html
    assert "third-party" in html  # footer disclosure inside the widget


def test_site_page_links_to_login(jinja_env):
    html = render(jinja_env, "site.html", customer_no=None)
    assert 'href="/login"' in html


# --------------------------------------------------------------------------------------
# Hard rule: no hardcoded company/backend hostname anywhere, only relative URLs.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative_path",
    ["site.html", "login.html", "_widget.html"],
)
def test_template_source_has_no_absolute_backend_url(relative_path):
    source = (TEMPLATES_DIR / relative_path).read_text(encoding="utf-8")
    assert not _ABSOLUTE_URL_ATTR_RE.search(source), (
        f"{relative_path} appears to hardcode an absolute URL in an href/src/action attribute"
    )
    assert not _SUSPICIOUS_HOST_RE.search(source), (
        f"{relative_path} appears to reference a hardcoded host"
    )


def test_widget_js_only_calls_relative_endpoints():
    source = (STATIC_DIR / "widget.js").read_text(encoding="utf-8")
    assert not _ABSOLUTE_URL_JS_RE.search(source), (
        "widget.js must call the assistant API via relative URLs only"
    )
    assert not _SUSPICIOUS_HOST_RE.search(source)

    # every documented endpoint (contracts §4.2) must be referenced, as a relative path
    for endpoint in (
        '"/api/login"',
        '"/api/chat"',
        "/api/chat/stream",
        "/api/approvals/",
        "/audit",
    ):
        assert endpoint in source, f"widget.js does not appear to call {endpoint}"


# --------------------------------------------------------------------------------------
# stylesheets
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("stylesheet", ["widget.css", "site.css"])
def test_the_hidden_attribute_actually_hides(stylesheet) -> None:
    """The widget hides things with the HTML `hidden` attribute, and an author rule such as
    `.nh-widget__panel { display: flex }` outranks the user agent's `[hidden]` rule — which
    silently turned every "hide" into a no-op: the chat panel would not close and the typing
    dots stayed on screen from page load. Any stylesheet here must assert `hidden` wins.
    """
    raw = (STATIC_DIR / stylesheet).read_text(encoding="utf-8")
    # Comments explain the rule and mention `[hidden]` themselves, so strip them first.
    css = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    assert "[hidden]" in css, f"{stylesheet} must make the hidden attribute authoritative"
    block = css.split("[hidden]", 1)[1].split("}", 1)[0]
    assert "display" in block and "none" in block and "!important" in block, (
        f"{stylesheet}'s [hidden] rule must be `display: none !important`"
    )


def test_every_element_the_widget_hides_is_in_the_markup() -> None:
    """`setTyping`/`closePanel`/`showError` toggle `hidden` on these ids; a renamed id would
    make the toggle silently do nothing, exactly like the CSS bug above."""
    markup = (TEMPLATES_DIR / "_widget.html").read_text(encoding="utf-8")
    for element_id in ("chat-panel", "typing-indicator", "chat-error", "chat-launcher-badge"):
        assert f'id="{element_id}"' in markup, f"{element_id} is missing from the widget markup"
        # and it must start hidden, so nothing flashes before the first interaction
        if element_id != "chat-panel":
            segment = markup.split(f'id="{element_id}"', 1)[1].split(">", 1)[0]
            assert "hidden" in segment, f"{element_id} should start hidden"
