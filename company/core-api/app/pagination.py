"""Offset/limit pagination helper shared by list endpoints.

Where this sits: used by routers that return `{items, total}`.

Read next: `routers/customers.py`.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select


def paginate(db: Session, stmt: Select, *, offset: int, limit: int) -> tuple[list, int]:
    """Return one page of rows and the total count of the unpaginated query."""
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.offset(offset).limit(limit)).all()
    return list(rows), total
