from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.auth import require_scope

from app.auth_registry import get_registry
from app.db import get_db
from app.models import Region
from app.serializers import region_out

router = APIRouter(tags=["regions"])


@router.get("/v1/regions", dependencies=[Depends(require_scope("incidents:read", get_registry()))])
def list_regions(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(Region).order_by(Region.code)).all()
    return {"items": [region_out(r) for r in rows], "total": len(rows)}
