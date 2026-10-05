from __future__ import annotations

from fastapi.staticfiles import StaticFiles

from shared.app_factory import create_app
from shared.auth import ApiKeyRegistry
from shared.db import make_engine, make_session_factory

from app.bootstrap import run_bootstrap
from app.routers.api import router as api_router
from app.routers.ui import router as ui_router
from app.settings import TicketingSettings, get_settings


def build_app(settings: TicketingSettings | None = None):
    settings = settings or get_settings()

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    registry = ApiKeyRegistry()
    registry.register(settings.ticketing_api_key, "ticketing-client", {"tickets:*"})

    def on_startup() -> None:
        run_bootstrap(engine, settings)

    app = create_app(
        service_name=settings.service_name,
        version=settings.service_version,
        title="NetHız Ticketing",
        description="Department ticketing service (Jira/Zendesk stand-in) for NetHız Telekom.",
        on_startup=on_startup,
        log_level=settings.log_level,
    )

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.api_key_registry = registry

    app.mount("/static", StaticFiles(directory=str(_static_dir())), name="static")
    app.include_router(api_router)
    app.include_router(ui_router)

    return app


def _static_dir():
    from pathlib import Path

    return Path(__file__).resolve().parent / "static"


app = build_app()
