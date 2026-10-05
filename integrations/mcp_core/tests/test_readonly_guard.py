"""mcp-core never issues anything but SELECT against the diagnostic database.
`app.diag.query_diag` is the only way mcp-core's tools touch the DB, so a
non-SELECT statement must be refused here too (defense in depth on top of
`common/tests/test_readonly_db.py` and the `readonly_diag` Postgres role
itself).
"""
from __future__ import annotations

import pytest

from app.diag import DiagQueryError, query_diag


def test_non_select_statement_is_refused_before_reaching_the_driver() -> None:
    with pytest.raises(DiagQueryError):
        query_diag("DELETE FROM core.customers")


def test_every_read_tool_sql_starts_with_select() -> None:
    import app.tools_read as tools_read

    sql_constants = [
        value
        for name, value in vars(tools_read).items()
        if name.endswith("_SQL") and isinstance(value, str)
    ]
    assert len(sql_constants) >= 10  # one per diag-backed read tool
    for sql in sql_constants:
        assert sql.strip().upper().startswith("SELECT")
