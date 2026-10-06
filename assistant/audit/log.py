"""Tamper-evident, hash-chained audit trail (contracts §1.5, §4.8).

Import as: ``from audit.log import AuditLog, AuditEntry, ChainVerification``.
The pure hash-chain primitives (`canonical_json`, `compute_entry_hash`) are importable and
testable without any database.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from core_common.db import session_scope
from core_common.models import AuditEntryRow
from core_common.types import StepType
from privacy.masking import assert_no_pii

# The first entry in any conversation's chain has no predecessor.
GENESIS_HASH = "0" * 64


# --------------------------------------------------------------------------------------
# Pure hash-chain primitives (no I/O) — see assistant/audit/tests/test_hash_chain.py
# --------------------------------------------------------------------------------------


def canonical_json(payload: dict[str, Any]) -> bytes:
    """Sorted-keys, separator-tight, UTF-8 JSON — the deterministic input to hashing."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def compute_entry_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    """``entry_hash = sha256(prev_hash || canonical_json(entry_without_hash))`` (contracts §1.5)."""
    return hashlib.sha256(prev_hash.encode("utf-8") + canonical_json(payload)).hexdigest()


def digest_tool_output(output: Any) -> str:
    """sha256 of a tool's output, for `audit_entries.tool_output_digest` — never store the
    raw output itself (contracts §4.8)."""
    wrapped = output if isinstance(output, dict) else {"value": output}
    return hashlib.sha256(canonical_json(wrapped)).hexdigest()


def _entry_payload(
    *,
    seq: int,
    conversation_id: str,
    tenant: str,
    step_type: str,
    actor: str,
    summary: str,
    reason: str | None,
    evidence: dict[str, Any] | None,
    policy_decision: dict[str, Any] | None,
    tool_name: str | None,
    tool_input: dict[str, Any] | None,
    tool_output_digest: str | None,
    created_at: str,
) -> dict[str, Any]:
    return {
        "seq": seq,
        "conversation_id": conversation_id,
        "tenant": tenant,
        "step_type": step_type,
        "actor": actor,
        "summary": summary,
        "reason": reason,
        "evidence": evidence or {},
        "policy_decision": policy_decision,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output_digest": tool_output_digest,
        "created_at": created_at,
    }


# --------------------------------------------------------------------------------------
# Domain types returned by AuditLog
# --------------------------------------------------------------------------------------


class AuditEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    seq: int
    conversation_id: str
    tenant: str
    step_type: str
    actor: str
    summary: str
    reason: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    policy_decision: dict[str, Any] | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output_digest: str | None = None
    prev_hash: str | None = None
    entry_hash: str
    created_at: dt.datetime


class ChainVerification(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    broken_at: int | None = None  # the `id` of the first entry whose hash no longer matches


def _row_to_entry(row: AuditEntryRow) -> AuditEntry:
    return AuditEntry(
        id=row.id,
        seq=row.seq,
        conversation_id=row.conversation_id,
        tenant=row.tenant,
        step_type=row.step_type,
        actor=row.actor,
        summary=row.summary,
        reason=row.reason,
        evidence=row.evidence or {},
        policy_decision=row.policy_decision,
        tool_name=row.tool_name,
        tool_input=row.tool_input,
        tool_output_digest=row.tool_output_digest,
        prev_hash=row.prev_hash,
        entry_hash=row.entry_hash,
        created_at=dt.datetime.fromisoformat(row.created_at),
    )


def _row_payload(row: AuditEntryRow) -> dict[str, Any]:
    return _entry_payload(
        seq=row.seq,
        conversation_id=row.conversation_id,
        tenant=row.tenant,
        step_type=row.step_type,
        actor=row.actor,
        summary=row.summary,
        reason=row.reason,
        evidence=row.evidence,
        policy_decision=row.policy_decision,
        tool_name=row.tool_name,
        tool_input=row.tool_input,
        tool_output_digest=row.tool_output_digest,
        created_at=row.created_at,
    )


# --------------------------------------------------------------------------------------
# AuditLog
# --------------------------------------------------------------------------------------


class AuditLog:
    """Append-only trail for one conversation's reasoning/actions.

    `session_factory` is a SQLAlchemy `sessionmaker` (e.g. `core_common.db.get_sessionmaker()`
    for Postgres in production, or a plain `sessionmaker(bind=sqlite_engine)` in tests).
    """

    def __init__(self, session_factory: sessionmaker) -> None:
        self.session_factory = session_factory

    def append(
        self,
        conversation_id: str,
        step_type: StepType | str,
        summary: str,
        reason: str | None,
        evidence: dict[str, Any] | None,
        *,
        tenant: str,
        actor: str = "assistant",
        policy_decision: dict[str, Any] | None = None,
        tool_name: str | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_output_digest: str | None = None,
    ) -> AuditEntry:
        """Append one hash-chained row. `evidence` must already be masked (UK GDPR) — this is
        enforced by `assert_no_pii` before anything is written."""
        evidence = evidence or {}
        assert_no_pii(evidence)

        step_type_value = step_type.value if isinstance(step_type, StepType) else str(step_type)
        created_at = dt.datetime.now(dt.timezone.utc).isoformat()

        with session_scope(self.session_factory) as session:
            prev_row = (
                session.query(AuditEntryRow)
                .filter_by(conversation_id=conversation_id)
                .order_by(AuditEntryRow.id.desc())
                .first()
            )
            prev_hash = prev_row.entry_hash if prev_row is not None else GENESIS_HASH
            next_seq = (session.query(func.coalesce(func.max(AuditEntryRow.seq), 0)).scalar() or 0) + 1

            payload = _entry_payload(
                seq=next_seq,
                conversation_id=conversation_id,
                tenant=tenant,
                step_type=step_type_value,
                actor=actor,
                summary=summary,
                reason=reason,
                evidence=evidence,
                policy_decision=policy_decision,
                tool_name=tool_name,
                tool_input=tool_input,
                tool_output_digest=tool_output_digest,
                created_at=created_at,
            )
            entry_hash = compute_entry_hash(prev_hash, payload)

            row = AuditEntryRow(
                seq=next_seq,
                conversation_id=conversation_id,
                tenant=tenant,
                step_type=step_type_value,
                actor=actor,
                summary=summary,
                reason=reason,
                evidence=evidence,
                policy_decision=policy_decision,
                tool_name=tool_name,
                tool_input=tool_input,
                tool_output_digest=tool_output_digest,
                prev_hash=prev_hash,
                entry_hash=entry_hash,
                created_at=created_at,
            )
            session.add(row)
            session.flush()
            return _row_to_entry(row)

    def verify_chain(self, conversation_id: str) -> ChainVerification:
        with session_scope(self.session_factory) as session:
            rows = (
                session.query(AuditEntryRow)
                .filter_by(conversation_id=conversation_id)
                .order_by(AuditEntryRow.id.asc())
                .all()
            )
            prev_hash = GENESIS_HASH
            for row in rows:
                if row.prev_hash != prev_hash:
                    return ChainVerification(ok=False, broken_at=row.id)
                expected = compute_entry_hash(prev_hash, _row_payload(row))
                if expected != row.entry_hash:
                    return ChainVerification(ok=False, broken_at=row.id)
                prev_hash = row.entry_hash
            return ChainVerification(ok=True, broken_at=None)

    def timeline(self, conversation_id: str) -> list[AuditEntry]:
        with session_scope(self.session_factory) as session:
            rows = (
                session.query(AuditEntryRow)
                .filter_by(conversation_id=conversation_id)
                .order_by(AuditEntryRow.id.asc())
                .all()
            )
            return [_row_to_entry(row) for row in rows]
