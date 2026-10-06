from __future__ import annotations

from fastapi import Request

from shared.app_factory import create_app

from app.bootstrap import bootstrap
from app.db import get_session_factory
from app.metrics_gauges import refresh_gauges
from app.routers import (
    appointments,
    billing,
    customers,
    incidents,
    notifications,
    packages,
    provisioning,
    regions,
    subscriptions,
    webhooks,
)
from app.settings import get_settings

settings = get_settings()


def _on_startup() -> None:
    bootstrap(settings)


app = create_app(
    service_name=settings.service_name,
    version=settings.service_version,
    title="NetSwift core-api",
    description="Subscriptions, payments, provisioning, incidents and notifications.",
    on_startup=_on_startup,
    log_level=settings.log_level,
)


@app.middleware("http")
async def _refresh_metrics_gauges(request: Request, call_next):
    if request.url.path == "/metrics":
        factory = get_session_factory()
        session = factory()
        try:
            refresh_gauges(session)
        except Exception:  # pragma: no cover - metrics must never break the request
            pass
        finally:
            session.close()
    return await call_next(request)


app.include_router(packages.router)
app.include_router(regions.router)
app.include_router(customers.router)
app.include_router(subscriptions.router)
app.include_router(billing.router)
app.include_router(provisioning.router)
app.include_router(appointments.router)
app.include_router(incidents.router)
app.include_router(notifications.router)
app.include_router(webhooks.router)
