from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import sessionmaker

from shared.auth import ApiKeyRegistry, require_scope
from shared.db import session_scope

from app import engine
from app.metrics_gauges import set_outage_gauge
from app.schemas import ControlFlagsPayload, ControlFlagsResponse
from app.settings import Settings


def _to_response(flags: dict) -> ControlFlagsResponse:
    return ControlFlagsResponse(
        failure_rate=float(flags.get("failure_rate", 0.0)),
        outage=bool(flags.get("outage", False)),
        latency_ms=int(flags.get("latency_ms", 0) or 0),
        force_failure_code=flags.get("force_failure_code"),
    )


def build_router(session_factory: sessionmaker, settings: Settings, registry: ApiKeyRegistry) -> APIRouter:
    router = APIRouter(prefix="/psp/v1", tags=["control"])
    require_key = require_scope("psp:access", registry)

    @router.get("/control")
    def get_control(_=Depends(require_key)) -> ControlFlagsResponse:
        with session_scope(session_factory) as session:
            flags = engine.get_control_flags(session)
            response = _to_response(flags)
        set_outage_gauge(response.outage)
        return response

    @router.post("/control")
    def set_control(body: ControlFlagsPayload, _=Depends(require_key)) -> ControlFlagsResponse:
        updates = body.model_dump(exclude_unset=True)
        with session_scope(session_factory) as session:
            flags = engine.set_control_flags(session, updates)
            response = _to_response(flags)
        set_outage_gauge(response.outage)
        return response

    return router
