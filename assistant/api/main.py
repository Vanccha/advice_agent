"""FastAPI entry point (contracts section 4.2): run with ``uvicorn api.main:app``.

Where this sits: the outermost layer of the assistant. uvicorn imports `app` from here;
`create_app()` mounts the routers from `api/routes/` and the static files from `web/`.
Collaborators are built in `api/state.py`, not here.

Key ideas:
- App factory: `create_app()` assembles the app in one readable place; this file contains
  no endpoint logic.
- Lifespan: startup work runs once when the server starts (`state.startup`), and a failure
  there degrades the assistant instead of preventing the process from starting.
- Routers per resource keep each URL family in its own small file.

Read next: `api/state.py`, then `api/routes/chat.py`.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from api import state as state_module
from api.errors import http_exception_handler
from api.routes import approvals, audit, auth, chat, ops, pages, webhooks
from api.state import state  # noqa: F401  (re-exported so `api.main.state` keeps working)
from api.templating import STATIC_DIR


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run startup wiring before serving the first request."""
    state_module.startup()
    yield


def create_app() -> FastAPI:
    """Build the FastAPI application: error handler, static files, then every router."""
    application = FastAPI(title="Support assistant API", lifespan=lifespan)
    application.add_exception_handler(HTTPException, http_exception_handler)
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    for module in (pages, auth, chat, approvals, audit, webhooks, ops):
        application.include_router(module.router)
    return application


app = create_app()
