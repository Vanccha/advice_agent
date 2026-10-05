from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.errors import NotFound

from app.db import get_db
from app.models import Package
from app.pagination import paginate
from app.serializers import package_out

router = APIRouter(tags=["packages"])


@router.get("/v1/packages")
def list_packages(
    profile: str | None = Query(default=None),
    max_price: float | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(Package)
    if profile:
        stmt = stmt.where(Package.target_profile == profile)
    if max_price is not None:
        stmt = stmt.where(Package.monthly_price_try <= max_price)
    if is_active is not None:
        stmt = stmt.where(Package.is_active == is_active)
    rows, total = paginate(db, stmt.order_by(Package.id), offset=offset, limit=limit)
    return {"items": [package_out(p) for p in rows], "total": total}


@router.get("/v1/packages/{code}")
def get_package(code: str, db: Session = Depends(get_db)) -> dict:
    pkg = db.scalar(select(Package).where(Package.code == code))
    if pkg is None:
        raise NotFound("PACKAGE_NOT_FOUND", f"Package '{code}' not found.")
    return package_out(pkg)
