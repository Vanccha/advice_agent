from __future__ import annotations

from pathlib import Path

from fastapi.staticfiles import StaticFiles

from shared.app_factory import create_app

from app.bootstrap import run_bootstrap
from app.routers import alertmanager, api, ui
from app.settings import get_settings

settings = get_settings()

app = create_app(
    service_name=settings.service_name,
    version=settings.service_version,
    title="NetHız Bildirim Merkezi",
    description="Teams/Slack benzeri dahili departman kanalları ve uyarı yayını.",
    on_startup=run_bootstrap,
    log_level=settings.log_level,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(api.router)
app.include_router(alertmanager.router)
app.include_router(ui.router)
