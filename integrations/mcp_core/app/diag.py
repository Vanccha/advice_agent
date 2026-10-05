"""Thin helper around `common.readonly_db` for the `diag.*` queries used by
`mcp-core`'s read tools: run a SELECT, never raise, map any failure
(connectivity, a bug that produced a non-SELECT statement, ...) into a
`ToolResult.fail("DIAG_DB_ERROR", ...)` instead of propagating.
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
    """Run one SELECT against the diag schema. Raises `DiagQueryError` on any
    failure (connectivity, read-only violation, ...); callers catch this and
    turn it into `ToolResult.fail`.
    """
    try:
        return fetch_all(diag_database_url(), sql, params or {})
    except Exception as exc:  # noqa: BLE001 - deliberately broad, always converted by the caller
        raise DiagQueryError(str(exc)) from exc
