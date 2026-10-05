"""Internal shim that loads the third-party `mcp` client SDK despite this very package
also being importable as top-level `mcp`.

Why this exists: `assistant/` is laid out so every immediate subdirectory is its own
top-level importable package (contracts §0 — `core_common`, `policy`, `llm`, `decision`,
`tickets`, ... and this one, `mcp`). That convention collides with the pip-installed `mcp`
client SDK, which is *also* importable as top-level `mcp`. Because the assistant's own
`assistant/` directory is prepended to `sys.path` (ahead of site-packages), a plain
``import mcp`` from inside this package resolves to *this package itself* — by the time any
submodule here runs, Python has already bound ``sys.modules["mcp"]`` to us while finishing
our own `__init__.py`. (Confirmed directly against the installed SDK before writing this:
see the test run that accompanies this change.)

Every other module in this package must obtain the SDK through this shim
(``from mcp._sdk import ClientSession, streamable_http_client``) — never ``import mcp``
directly, which would just reimport this package.

The workaround: temporarily evict any already-cached `mcp`/`mcp.*` modules and hide the
`sys.path` entries that would resolve back to this package, import the real SDK fresh
under those conditions, capture the two names we need, then restore `sys.path` and
`sys.modules` exactly as they were — so the rest of the assistant keeps importing *this*
package as `mcp` normally (`import mcp` from elsewhere still gets `ToolGateway`, `FakeGateway`,
etc., unaffected by this dance).

This is deliberately narrow and defensive: any failure here is caught and recorded in
`MCP_SDK_IMPORT_ERROR` rather than raised at import time, so `assistant.mcp` stays
importable (and testable with `FakeGateway`, no network, no real SDK needed) even if the
real SDK is ever missing or broken.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _load_real_mcp_sdk():
    this_package_dir = Path(__file__).resolve().parent
    assistant_root = this_package_dir.parent

    saved_path = list(sys.path)
    saved_modules = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "mcp" or name.startswith("mcp.")
    }
    for name in saved_modules:
        del sys.modules[name]

    def _is_self_path(entry: str) -> bool:
        try:
            resolved = Path(entry or ".").resolve()
        except OSError:  # pragma: no cover - defensive
            return False
        return resolved in (this_package_dir, assistant_root)

    sys.path = [p for p in sys.path if not _is_self_path(p)]

    try:
        real_mcp = importlib.import_module("mcp")
        real_streamable_http = importlib.import_module("mcp.client.streamable_http")
        client_session = real_mcp.ClientSession
        streamable_http_client = real_streamable_http.streamable_http_client
    finally:
        for name in list(sys.modules):
            if name == "mcp" or name.startswith("mcp."):
                del sys.modules[name]
        sys.modules.update(saved_modules)
        sys.path = saved_path

    return client_session, streamable_http_client


try:
    ClientSession, streamable_http_client = _load_real_mcp_sdk()
    MCP_SDK_AVAILABLE = True
    MCP_SDK_IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover - only if the SDK truly is not installed
    ClientSession = None  # type: ignore[assignment]
    streamable_http_client = None  # type: ignore[assignment]
    MCP_SDK_AVAILABLE = False
    MCP_SDK_IMPORT_ERROR = str(exc)
