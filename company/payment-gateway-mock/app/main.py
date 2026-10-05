from __future__ import annotations

from shared.app_factory import create_app
from shared.auth import ApiKeyRegistry
from shared.db import make_engine, make_session_factory

from app.bootstrap import bootstrap
from app.metrics_gauges import install_psp_metrics
from app.outage import install_outage_guard
from app.routers import charges as charges_router
from app.routers import control as control_router
from app.settings import get_settings

settings = get_settings()
engine = make_engine(settings.database_url)
session_factory = make_session_factory(engine)

registry = ApiKeyRegistry()
registry.register(settings.psp_api_key, "psp-client", {"psp:*"})


def _on_startup() -> None:
    bootstrap(engine, settings)


app = create_app(
    service_name=settings.service_name,
    version=settings.service_version,
    title="NetHız Payment Gateway (PSP simulator)",
    description="Simulated payment service provider: charges, refunds, webhooks, chaos control.",
    on_startup=_on_startup,
    log_level=settings.log_level,
)

app.include_router(charges_router.build_router(session_factory, settings, registry))
app.include_router(control_router.build_router(session_factory, settings, registry))

install_psp_metrics(app)
install_outage_guard(
    app,
    session_factory,
    service_name=settings.service_name,
    service_version=settings.service_version,
)
