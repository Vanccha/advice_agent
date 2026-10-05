from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from shared.clock import utcnow

SEQUENCE_NAME = "tkt.ticket_key_seq"


def next_ticket_key(session: Session) -> str:
    """Generate TKT-<year>-<5-digit sequence>, safe under concurrency.

    Uses a single Postgres sequence (`nextval` is atomic and never blocks concurrent
    callers) so two requests can never receive the same key, even under heavy load.
    """
    year = utcnow().year
    seq_value = session.execute(text(f"SELECT nextval('{SEQUENCE_NAME}')")).scalar_one()
    return f"TKT-{year}-{seq_value:05d}"
