"""Thin helper around `common.readonly_db` for `mcp-core`'s `diag.*` queries.

Where this sits: called by `tools_read.py`; calls the shared read-only DB layer.

Key ideas:
- Convert any failure (connectivity, a bug that produced a non-SELECT
  statement) into one adapter-local exception, so handlers can turn it into
  `ToolResult.fail("DIAG_DB_ERROR", ...)` instead of leaking a driver error.
- Each adapter keeps its own twin of this file because each resolves its own
  `DIAG_DATABASE_URL` through its own `deps` module.

Read next: `tools_read.py`.
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
    """Run one SELECT against the diag schema. Raises `DiagQueryError` on any
    failure (connectivity, read-only violation, ...); callers catch this and
    turn it into `ToolResult.fail`.
    """
    try:
        return fetch_all(diag_database_url(), sql, params or {})
    except Exception as exc:  # noqa: BLE001 - deliberately broad, always converted by the caller
        raise DiagQueryError(str(exc)) from exc
