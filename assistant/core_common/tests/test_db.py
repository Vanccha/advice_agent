from sqlalchemy.orm import sessionmaker

from core_common.db import AUDIT_APPEND_ONLY_TRIGGER_SQL, bootstrap_schema, create_sqlite_engine, session_scope
from core_common.models import Conversation


def test_append_only_trigger_ddl_text_present():
    """The append-only guarantee (contracts §1.5) must be a real DDL trigger, not just a comment."""
    assert "BEFORE UPDATE OR DELETE" in AUDIT_APPEND_ONLY_TRIGGER_SQL
    assert "RAISE EXCEPTION" in AUDIT_APPEND_ONLY_TRIGGER_SQL
    assert "audit_entries" in AUDIT_APPEND_ONLY_TRIGGER_SQL


def test_bootstrap_schema_and_session_scope_round_trip_sqlite():
    engine = create_sqlite_engine()
    bootstrap_schema(engine)
    sm = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    with session_scope(sm) as session:
        session.add(Conversation(conversation_id="conv-1", tenant="netswift", mode="ROUTER"))

    with session_scope(sm) as session:
        row = session.query(Conversation).filter_by(conversation_id="conv-1").one()
        assert row.tenant == "netswift"
        assert row.mode == "ROUTER"


def test_session_scope_rolls_back_on_exception():
    engine = create_sqlite_engine()
    bootstrap_schema(engine)
    sm = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    with session_scope(sm) as session:
        session.add(Conversation(conversation_id="conv-rollback", tenant="netswift"))

    try:
        with session_scope(sm) as session:
            session.add(Conversation(conversation_id="conv-2", tenant="netswift"))
            raise ValueError("boom")
    except ValueError:
        pass

    with session_scope(sm) as session:
        count = session.query(Conversation).filter_by(conversation_id="conv-2").count()
        assert count == 0
