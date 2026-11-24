"""Concurrent appends must not fork the hash chain or duplicate `seq`."""
from __future__ import annotations

import threading

from sqlalchemy.orm import sessionmaker

from audit.log import AuditLog
from core_common.db import bootstrap_schema, create_sqlite_engine, session_scope
from core_common.models import AuditEntryRow
from core_common.types import StepType


def test_concurrent_appends_keep_one_chain_and_unique_seq(tmp_path) -> None:
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    bootstrap_schema(engine)
    log = AuditLog(sessionmaker(bind=engine, expire_on_commit=False, future=True))

    errors: list[BaseException] = []

    def worker(conv: str) -> None:
        try:
            for i in range(15):
                log.append(conv, StepType.MODE_DECISION, f"step {i}", None, {}, tenant="netswift")
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)

    # Four threads on one conversation (chain contention) plus two on their own.
    threads = [threading.Thread(target=worker, args=("conv-shared",)) for _ in range(4)]
    threads += [threading.Thread(target=worker, args=(f"conv-own-{n}",)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert log.verify_chain("conv-shared").ok is True
    assert log.verify_chain("conv-own-0").ok is True
    assert log.verify_chain("conv-own-1").ok is True
    with session_scope(log.session_factory) as session:
        seqs = [r.seq for r in session.query(AuditEntryRow).all()]
    assert len(seqs) == 6 * 15
    assert len(set(seqs)) == len(seqs)
