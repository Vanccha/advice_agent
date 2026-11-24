"""Thin helper around `common.readonly_db` for `mcp-payment`'s one diag-backed tool.

Where this sits: called by `tools.py` (`get_payment_status`). It is the twin of
`mcp_core/app/diag.py`, kept separate because each server resolves its own
`DIAG_DATABASE_URL` through its own `deps` module.

Key ideas:
- Any failure becomes `DiagQueryError`, which the handler maps to a
  `DIAG_DB_ERROR` envelope.

Read next: `tools.py`.
"""
from __future__ import annotations

from typing import Any, Optional

from common.readonly_db import fetch_all

from .deps import diag_database_url


class DiagQueryError(Exception):
    """Any failure while querying `diag.*`; carries a message safe to put in an envelope."""
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def query_diag(sql: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """Run one SELECT against the diag schema; raises `DiagQueryError` on any failure."""
    try:
        return fetch_all(diag_database_url(), sql, params or {})
    except Exception as exc:  # noqa: BLE001 - deliberately broad, always converted by the caller
        raise DiagQueryError(str(exc)) from exc
