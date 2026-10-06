"""The audit trail is tamper-evident at the database level, not just in application code."""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def assistant_engine():
    url = os.environ.get("ASSISTANT_DATABASE_URL")
    if not url:
        pytest.skip("ASSISTANT_DATABASE_URL is not configured")
    from core_common.db import bootstrap_schema, get_engine

    engine = get_engine(url)
    try:
        bootstrap_schema(engine)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"assistant database unavailable: {exc}")
    return engine


@pytest.fixture
def conversation_id() -> str:
    return f"it-{uuid.uuid4().hex[:10]}"


def _append_two_steps(url: str, conversation_id: str):
    from audit.log import AuditLog
    from core_common.db import get_sessionmaker

    log = AuditLog(get_sessionmaker(url))
    log.append(
        conversation_id,
        "tool_call",
        "checked the subscription",
        "diagnosing a stuck provisioning job",
        {"record_ids": {"subscription_id": 201}},
        tenant="netswift",
    )
    log.append(
        conversation_id,
        "policy_check",
        "retry permitted",
        "policy allows retrying a stuck job",
        {"record_ids": {"job_id": 7}},
        tenant="netswift",
    )
    return log


def test_chain_verifies_after_appending(assistant_engine, conversation_id) -> None:
    log = _append_two_steps(os.environ["ASSISTANT_DATABASE_URL"], conversation_id)
    verification = log.verify_chain(conversation_id)
    assert verification.ok, f"hash chain broken at {verification.broken_at}"
    assert len(log.timeline(conversation_id)) == 2


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_database_refuses_to_rewrite_history(assistant_engine, conversation_id, operation) -> None:
    _append_two_steps(os.environ["ASSISTANT_DATABASE_URL"], conversation_id)
    statement = {
        "update": "update asst.audit_entries set summary = 'tampered' where conversation_id = :cid",
        "delete": "delete from asst.audit_entries where conversation_id = :cid",
    }[operation]
    with pytest.raises(Exception) as excinfo:
        with assistant_engine.begin() as conn:
            conn.execute(text(statement), {"cid": conversation_id})
    assert "append-only" in str(excinfo.value).lower()
