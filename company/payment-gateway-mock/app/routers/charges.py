from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from sqlalchemy.orm import Session, sessionmaker

from shared.auth import ApiKeyRegistry, require_scope
from shared.clock import isoformat
from shared.db import session_scope

from app import engine
from app.metrics_gauges import psp_charges_total, psp_refunds_total
from app.models import Charge
from app.schemas import (
    ChargeCreateRequest,
    ChargeListResponse,
    ChargeResponse,
    RefundCreateRequest,
    RefundResponse,
)
from app.settings import Settings
from app.webhooks import build_event_payload, deliver_webhook


def _charge_to_response(charge: Charge) -> ChargeResponse:
    return ChargeResponse(
        charge_ref=charge.charge_ref,
        status=charge.status,
        customer_ref=charge.customer_ref,
        amount_try=float(charge.amount_try),
        method=charge.method,
        card_last4=charge.card_last4,
        failure_code=charge.failure_code,
        failure_message=charge.failure_message,
        idempotency_key=charge.idempotency_key,
        created_at=isoformat(charge.created_at),
        updated_at=isoformat(charge.updated_at),
    )


def build_router(
    session_factory: sessionmaker,
    settings: Settings,
    registry: ApiKeyRegistry,
) -> APIRouter:
    router = APIRouter(prefix="/psp/v1", tags=["charges"])
    require_key = require_scope("psp:access", registry)

    def get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    @router.post("/charges", status_code=201)
    def create_charge(
        body: ChargeCreateRequest,
        response: Response,
        background_tasks: BackgroundTasks,
        _=Depends(require_key),
    ) -> ChargeResponse:
        with session_scope(session_factory) as scoped_session:
            charge, created = engine.create_charge(
                scoped_session,
                default_failure_rate=settings.psp_failure_rate,
                amount_try=body.amount_try,
                customer_ref=body.customer_ref,
                method=body.method,
                card_token=body.card_token,
                idempotency_key=body.idempotency_key,
                callback_url=body.callback_url,
            )
            charge_response = _charge_to_response(charge)
            charge_id = charge.id

        if not created:
            response.status_code = 200

        psp_charges_total.labels(charge_response.status).inc()

        if created:
            payload = build_event_payload("charge.updated", charge)
            background_tasks.add_task(
                deliver_webhook,
                session_factory,
                settings,
                charge_id=charge_id,
                payload=payload,
            )

        return charge_response

    @router.get("/charges/{charge_ref}")
    def get_charge(
        charge_ref: str,
        session: Session = Depends(get_session),
        _=Depends(require_key),
    ) -> ChargeResponse:
        charge = engine.get_charge_by_ref(session, charge_ref)
        return _charge_to_response(charge)

    @router.get("/charges")
    def list_charges(
        customer_ref: str | None = None,
        status: str | None = None,
        limit: int = Query(default=50, le=200),
        offset: int = Query(default=0, ge=0),
        session: Session = Depends(get_session),
        _=Depends(require_key),
    ) -> ChargeListResponse:
        items, total = engine.list_charges(
            session, customer_ref=customer_ref, status=status, limit=limit, offset=offset
        )
        return ChargeListResponse(items=[_charge_to_response(c) for c in items], total=total)

    @router.post("/charges/{charge_ref}/refunds", status_code=201)
    def create_refund(
        charge_ref: str,
        body: RefundCreateRequest,
        background_tasks: BackgroundTasks,
        _=Depends(require_key),
    ) -> RefundResponse:
        with session_scope(session_factory) as scoped_session:
            charge = engine.get_charge_by_ref(scoped_session, charge_ref)
            refund = engine.refund_charge(
                scoped_session, charge, amount_try=body.amount_try, reason=body.reason
            )
            refund_response = RefundResponse(
                refund_ref=refund.refund_ref,
                charge_ref=charge.charge_ref,
                amount_try=float(refund.amount_try),
                status=refund.status,
                reason=refund.reason,
                created_at=isoformat(refund.created_at),
                charge_status=charge.status,
            )
            charge_id = charge.id
            charge_snapshot = charge

        psp_refunds_total.labels(refund_response.status).inc()

        payload = build_event_payload("refund.completed", charge_snapshot)
        background_tasks.add_task(
            deliver_webhook,
            session_factory,
            settings,
            charge_id=charge_id,
            payload=payload,
        )

        return refund_response

    return router
