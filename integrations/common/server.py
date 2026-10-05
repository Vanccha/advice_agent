"""`build_server(...)` — the one way every MCP server in this layer is built,
so `mcp-core`, `mcp-payment`, `mcp-ticketing`, `mcp-monitoring` and
`mcp-notification` all expose the same shape: `GET /health`, `GET /metrics`
and the MCP streamable-HTTP endpoint mounted at `POST /mcp`.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

from fastapi import APIRouter, FastAPI
from fastapi.responses import Response
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

ToolsRegistrar = Callable[[MCPServer], None]


def build_server(
    name: str,
    version: str,
    tools_registrar: ToolsRegistrar,
    extra_routes: APIRouter | None = None,
) -> FastAPI:
    """Build the FastAPI app for one MCP server.

    `tools_registrar(mcp)` is called once, before the streamable-HTTP ASGI app
    is built, to register every tool via `common.tool_spec.register_tool`.

    `extra_routes` carries any plain (non-MCP) HTTP endpoints the adapter needs —
    `mcp-monitoring`'s Alertmanager receiver, for instance. It is included *before*
    the MCP app is mounted at "/", because that mount is a catch-all: anything
    registered after it is unreachable.
    """
    mcp = MCPServer(name=name, version=version)
    tools_registrar(mcp)

    # streamable_http_app() returns a Starlette app with its own route at
    # "/mcp" (the default `streamable_http_path`) and its own lifespan that
    # starts the StreamableHTTP session manager's task group. Mounting it at
    # FastAPI root ("/") keeps the external path exactly `POST /mcp`, and
    # routes registered on `app` before the mount (health, metrics) are
    # matched first since Starlette tries routes in registration order.
    # The installed mcp SDK auto-enables DNS-rebinding "Host:" header
    # protection (allowing only 127.0.0.1/localhost/[::1]) whenever it thinks
    # it's bound to a loopback address. These servers are only ever reached
    # over the compose network (by compose DNS name, e.g. "mcp-core:8000")
    # or via a host port mapping for the demo — never by an untrusted
    # browser, which is what that protection defends against — so it's
    # disabled here rather than fighting it with an allow-list.
    mcp_asgi_app = mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Required for the installed mcp SDK (2.x `MCPServer`): the session
        # manager's task group must be running for streamable-HTTP sessions
        # to work at all. See MCPServer.session_manager's own docstring
        # ("exposed to enable ... mounting ... in a single FastAPI
        # application"), which only works after streamable_http_app() above
        # has been called.
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title=name, version=version, lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": name, "version": version}

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    if extra_routes is not None:
        app.include_router(extra_routes)

    app.mount("/", mcp_asgi_app)

    return app
