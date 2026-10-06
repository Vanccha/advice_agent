from __future__ import annotations

import random
import time
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.errors import ApiError, Conflict, NotFound

from app.models import Charge, ControlFlag, Refund

FAILURE_CODES = [
    "INSUFFICIENT_FUNDS",
    "CARD_DECLINED",
    "DO_NOT_HONOR",
    "TIMEOUT",
    "GATEWAY_ERROR",
]

FAILURE_MESSAGES = {
    "INSUFFICIENT_FUNDS": "The card does not have sufficient funds.",
    "CARD_DECLINED": "The card was declined by the issuer.",
    "DO_NOT_HONOR": "The issuer returned a do-not-honor response.",
    "TIMEOUT": "The request timed out upstream.",
    "GATEWAY_ERROR": "The payment gateway returned an unexpected error.",
}

DEFAULT_FLAGS = {
    "failure_rate": 0.08,
    "outage": False,
    "latency_ms": 0,
    "force_failure_code": None,
}


def new_charge_ref() -> str:
    return f"ch_{uuid.uuid4().hex[:20]}"


def new_refund_ref() -> str:
    return f"rf_{uuid.uuid4().hex[:20]}"


def last4_from_token(card_token: str | None) -> str | None:
    if not card_token:
        return None
    digits = "".join(ch for ch in card_token if ch.isdigit())
    if not digits:
        digits = str(abs(hash(card_token)) % 10000)
    return digits[-4:].zfill(4)


def get_control_flags(session: Session) -> dict:
    flags = dict(DEFAULT_FLAGS)
    for row in session.execute(select(ControlFlag)).scalars():
        flags[row.key] = row.value
    return flags


def set_control_flags(session: Session, updates: dict) -> dict:
    for key, value in updates.items():
        if value is None and key != "force_failure_code":
            continue
        row = session.get(ControlFlag, key)
        if row is None:
            row = ControlFlag(key=key, value=value)
            session.add(row)
        else:
            row.value = value
    session.flush()
    return get_control_flags(session)


def is_outage(session: Session) -> bool:
    return bool(get_control_flags(session).get("outage", False))


def find_by_idempotency_key(session: Session, idempotency_key: str) -> Charge | None:
    return session.execute(
        select(Charge).where(Charge.idempotency_key == idempotency_key)
    ).scalar_one_or_none()


def create_charge(
    session: Session,
    *,
    default_failure_rate: float,
    amount_gbp: float,
    customer_ref: str,
    method: str,
    card_token: str | None,
    idempotency_key: str,
    callback_url: str | None,
) -> tuple[Charge, bool]:
    """Returns (charge, created). created=False means an existing charge was replayed."""
    existing = find_by_idempotency_key(session, idempotency_key)
    if existing is not None:
        return existing, False

    flags = get_control_flags(session)
    latency_ms = flags.get("latency_ms") or 0
    if latency_ms:
        time.sleep(min(float(latency_ms), 10_000) / 1000.0)

    force_code = flags.get("force_failure_code")
    failure_rate = flags.get("failure_rate")
    if failure_rate is None:
        failure_rate = default_failure_rate

    if force_code:
        status = "failed"
        failure_code = force_code
        failure_message = FAILURE_MESSAGES.get(force_code, "Forced failure.")
    elif random.random() < float(failure_rate):
        failure_code = random.choice(FAILURE_CODES)
        status = "failed"
        failure_message = FAILURE_MESSAGES[failure_code]
    else:
        status = "succeeded"
        failure_code = None
        failure_message = None

    charge = Charge(
        charge_ref=new_charge_ref(),
        customer_ref=customer_ref,
        amount_gbp=Decimal(str(amount_gbp)),
        status=status,
        method=method,
        card_last4=last4_from_token(card_token),
        idempotency_key=idempotency_key,
        failure_code=failure_code,
        failure_message=failure_message,
        callback_url=callback_url,
    )
    session.add(charge)
    session.flush()
    return charge, True


def get_charge_by_ref(session: Session, charge_ref: str) -> Charge:
    charge = session.execute(
        select(Charge).where(Charge.charge_ref == charge_ref)
    ).scalar_one_or_none()
    if charge is None:
        raise NotFound("CHARGE_NOT_FOUND", f"No charge with ref '{charge_ref}'.")
    return charge


def list_charges(
    session: Session,
    *,
    customer_ref: str | None,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Charge], int]:
    stmt = select(Charge)
    if customer_ref:
        stmt = stmt.where(Charge.customer_ref == customer_ref)
    if status:
        stmt = stmt.where(Charge.status == status)
    total = len(session.execute(stmt).scalars().all())
    stmt = stmt.order_by(Charge.id.desc()).limit(limit).offset(offset)
    items = list(session.execute(stmt).scalars().all())
    return items, total


def total_refunded(session: Session, charge_id: int) -> Decimal:
    rows = session.execute(
        select(Refund).where(Refund.charge_id == charge_id, Refund.status == "completed")
    ).scalars()
    return sum((r.amount_gbp for r in rows), Decimal("0"))


def refund_charge(
    session: Session, charge: Charge, *, amount_gbp: float, reason: str | None
) -> Refund:
    if charge.status == "refunded":
        raise Conflict("ALREADY_REFUNDED", f"Charge '{charge.charge_ref}' is already fully refunded.")
    if charge.status not in ("succeeded", "partially_refunded"):
        raise Conflict(
            "ALREADY_REFUNDED",
            f"Charge '{charge.charge_ref}' is in status '{charge.status}' and cannot be refunded.",
        )

    amount = Decimal(str(amount_gbp))
    already = total_refunded(session, charge.id)
    if already + amount > charge.amount_gbp:
        raise ApiError(
            "REFUND_EXCEEDS_CHARGE",
            "The refund amount exceeds the charge amount.",
            status_code=422,
            details={
                "charge_amount_gbp": float(charge.amount_gbp),
                "already_refunded_gbp": float(already),
                "requested_gbp": float(amount),
            },
        )

    refund = Refund(
        refund_ref=new_refund_ref(),
        charge_id=charge.id,
        amount_gbp=amount,
        status="completed",
        reason=reason,
    )
    session.add(refund)

    new_total = already + amount
    charge.status = "refunded" if new_total >= charge.amount_gbp else "partially_refunded"

    session.flush()
    return refund
