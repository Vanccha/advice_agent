from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker

from app.engine import is_outage
from app.metrics_gauges import set_outage_gauge

logger = logging.getLogger(__name__)

# Always reachable, even during a simulated outage.
PASSTHROUGH_PATHS = {"/metrics", "/psp/v1/control"}


def install_outage_guard(
    app: FastAPI,
    session_factory: sessionmaker,
    *,
    service_name: str,
    service_version: str,
) -> None:
    @app.middleware("http")
    async def _outage_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        try:
            session = session_factory()
            try:
                outage = is_outage(session)
            finally:
                session.close()
        except Exception:  # noqa: BLE001 - never let flag lookup break requests
            logger.exception("failed to read control flags; assuming no outage")
            outage = False

        set_outage_gauge(outage)

        if outage and path == "/health":
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "service": service_name, "version": service_version},
            )
        if outage and path not in PASSTHROUGH_PATHS:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "GATEWAY_UNAVAILABLE",
                        "message": "Payment gateway is currently unavailable.",
                        "details": {},
                    }
                },
            )
        return await call_next(request)
