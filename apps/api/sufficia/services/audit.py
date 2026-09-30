"""Append-only, hash-chained audit log (Document 03 §108-109).

event_hash = keccak(AUDIT_DOMAIN || 0x00 || prev_hash || canonical({type, subject, created_at, payload}))
Tampering with, deleting or reordering any row breaks every later hash; ``verify_chain`` detects it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from eth_utils import keccak
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..canonical.canonicalize import canonical_bytes, canonical_json, loads
from ..canonical.hashes import AUDIT_DOMAIN, hx, unhex32
from ..db.models import AuditEventRow

GENESIS = "0x" + "00" * 32

EVENT_TYPES = frozenset(
    {
        "INTENT_CREATED",
        "CONTRACT_FROZEN",
        "EVIDENCE_ACQUIRED",
        "EVIDENCE_REJECTED",
        "EVIDENCE_INJECTED",
        "DECISION_CREATED",
        "AUTHORIZATION_ISSUED",
        "AUTHORIZATION_REFUSED",
        "AUTHORIZATION_PUBLISHED",
        "AUTHORIZATION_CONSUMED",
        "AUTHORIZATION_EXPIRED",
        "EXECUTION_BLOCKED",
        "EXECUTION_SUBMITTED",
        "EXECUTION_CONFIRMED",
        "EXECUTION_FAILED",
        "RECEIPT_CREATED",
        "RECEIPT_COMMITTED",
        "RECEIPT_REPLAYED",
        "DEMO_MARKET_CHANGED",
        "DEMO_SCENARIO_RUN",
    }
)


def _hash(prev_hash: str, event_type: str, subject_id: str, created_at: datetime, payload: Any) -> str:
    body = canonical_bytes({"type": event_type, "subject": subject_id, "created_at": created_at, "payload": payload})
    return hx(keccak(AUDIT_DOMAIN + b"\x00" + unhex32(prev_hash) + body))


def append(session: Session, event_type: str, subject_id: str, created_at: datetime, payload: Any = None) -> AuditEventRow:
    """Must be called under Runtime.audit_lock (single writer keeps the chain linear)."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown audit event {event_type}")
    last = session.execute(select(AuditEventRow).order_by(AuditEventRow.seq.desc()).limit(1)).scalar_one_or_none()
    prev = last.event_hash if last else GENESIS
    payload = payload or {}
    row = AuditEventRow(
        event_type=event_type,
        subject_id=subject_id,
        created_at=created_at,
        payload=canonical_json(payload),
        prev_hash=prev,
        event_hash=_hash(prev, event_type, subject_id, created_at, loads(canonical_json(payload))),
    )
    session.add(row)
    session.flush()
    return row


def verify_chain(session: Session) -> dict[str, Any]:
    rows = session.execute(select(AuditEventRow).order_by(AuditEventRow.seq)).scalars().all()
    prev = GENESIS
    for row in rows:
        expected = _hash(prev, row.event_type, row.subject_id, row.created_at, loads(row.payload))
        if row.prev_hash != prev or row.event_hash != expected:
            return {"valid": False, "events": len(rows), "broken_at": row.seq, "head": prev}
        prev = row.event_hash
    return {"valid": True, "events": len(rows), "broken_at": None, "head": prev}


def to_dict(row: AuditEventRow) -> dict[str, Any]:
    return {
        "seq": row.seq,
        "event_type": row.event_type,
        "subject_id": row.subject_id,
        "created_at": row.created_at,
        "payload": loads(row.payload),
        "prev_hash": row.prev_hash,
        "event_hash": row.event_hash,
    }
