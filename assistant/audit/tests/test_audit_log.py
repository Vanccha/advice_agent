import pytest
from sqlalchemy.orm import sessionmaker

from audit.log import AuditLog
from core_common.db import bootstrap_schema, create_sqlite_engine, session_scope
from core_common.models import AuditEntryRow
from core_common.types import StepType
from privacy.masking import PIILeakError


@pytest.fixture()
def audit_log() -> AuditLog:
    engine = create_sqlite_engine()
    bootstrap_schema(engine)
    sm = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return AuditLog(sm)


def test_append_builds_a_valid_chain(audit_log: AuditLog):
    e1 = audit_log.append(
        "conv-1", StepType.MODE_DECISION, "router chose advisory", "intent=advisory", {},
        tenant="netswift",
    )
    e2 = audit_log.append(
        "conv-1", StepType.POLICY_CHECK, "checked retry_provisioning_job", "ok", {},
        tenant="netswift",
    )
    assert e1.prev_hash == "0" * 64
    assert e2.prev_hash == e1.entry_hash
    assert e1.seq < e2.seq

    verification = audit_log.verify_chain("conv-1")
    assert verification.ok is True
    assert verification.broken_at is None


def test_timeline_returns_entries_in_order(audit_log: AuditLog):
    audit_log.append("conv-2", StepType.MODE_DECISION, "first", None, {}, tenant="netswift")
    audit_log.append("conv-2", StepType.ACTION, "second", None, {}, tenant="netswift")

    entries = audit_log.timeline("conv-2")
    assert [e.summary for e in entries] == ["first", "second"]


def test_tampering_with_middle_entry_breaks_verification(audit_log: AuditLog):
    audit_log.append("conv-3", StepType.MODE_DECISION, "first", None, {}, tenant="netswift")
    middle = audit_log.append("conv-3", StepType.POLICY_CHECK, "second", None, {}, tenant="netswift")
    audit_log.append("conv-3", StepType.ACTION, "third", None, {}, tenant="netswift")

    assert audit_log.verify_chain("conv-3").ok is True

    # Simulate tampering directly at the storage layer (bypassing the append-only trigger,
    # which only exists under Postgres — see core_common/tests/test_db.py).
    with session_scope(audit_log.session_factory) as session:
        row = session.query(AuditEntryRow).filter_by(id=middle.id).one()
        row.summary = "tampered summary"

    verification = audit_log.verify_chain("conv-3")
    assert verification.ok is False
    assert verification.broken_at == middle.id


def test_append_rejects_raw_pii_in_evidence(audit_log: AuditLog):
    with pytest.raises(PIILeakError):
        audit_log.append(
            "conv-4",
            StepType.DECISION_SERVICE,
            "looked up customer",
            None,
            {"observations": ["matched NI number QQ123456C"]},
            tenant="netswift",
        )
