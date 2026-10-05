"""Thin helper around `common.readonly_db` for `mcp-payment`'s one diag-backed
tool (`get_payment_status`). See `mcp_core/app/diag.py` for the twin
implementation; kept separate (not shared) because each server resolves its
own `DIAG_DATABASE_URL` through its own `deps` module.
"""
from __future__ import annotations

from typing import Any, Optional

from common.readonly_db import fetch_all

from .deps import diag_database_url


class DiagQueryError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def query_diag(sql: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    try:
        return fetch_all(diag_database_url(), sql, params or {})
    except Exception as exc:  # noqa: BLE001 - deliberately broad, always converted by the caller
        raise DiagQueryError(str(exc)) from exc
