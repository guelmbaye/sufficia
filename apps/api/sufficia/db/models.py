"""Relational schema (Document 02 §38 + Document 04 §82-83 + Document 03 §108).

Authority-relevant objects are stored twice on purpose:
  * indexed columns for querying (state, hashes, status, timestamps);
  * a ``payload`` column holding the exact canonical JSON of the domain object, so hashes and replays
    are recomputed from byte-identical snapshots, never from re-typed columns.

Every table is append-only except the small status fields of authorizations / executions / receipts
and chain transactions, whose transitions are recorded in the audit chain.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class UTCDateTimeType(TypeDecorator):
    """Always timezone-aware UTC in Python, whatever the backend (SQLite drops tzinfo on its own).

    Hashes are recomputed from values read back from the database, so a naive datetime would change
    the canonical encoding; naive values are refused on write.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # type: ignore[override]
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetimes cannot be persisted")
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):  # type: ignore[override]
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


UTCDateTime = UTCDateTimeType()


class IntentRow(Base):
    __tablename__ = "intents"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    network: Mapped[str] = mapped_column(String(32))
    intent_hash: Mapped[str] = mapped_column(String(66), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    payload: Mapped[str] = mapped_column(Text)


class ContractRow(Base):
    __tablename__ = "evidence_contracts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("intents.id"), unique=True)
    contract_hash: Mapped[str] = mapped_column(String(66), index=True)
    compiler: Mapped[str] = mapped_column(String(16))
    frozen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    payload: Mapped[str] = mapped_column(Text)  # canonical EvidenceContract
    preservation: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)


class RequirementRow(Base):
    __tablename__ = "requirements"
    __table_args__ = (UniqueConstraint("contract_id", "requirement_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("evidence_contracts.id"), index=True)
    requirement_id: Mapped[str] = mapped_column(String(64))
    claim: Mapped[str] = mapped_column(String(64))
    operator: Mapped[str] = mapped_column(String(16))
    threshold: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    max_age_seconds: Mapped[int] = mapped_column(Integer)
    required: Mapped[bool] = mapped_column()
    origin: Mapped[str] = mapped_column(String(16))


class EvidenceSetRow(Base):
    """An immutable, versioned evidence set of a contract (an acquisition run or a Demo Lab injection)."""

    __tablename__ = "evidence_sets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("evidence_contracts.id"), index=True)
    parent_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    origin: Mapped[str] = mapped_column(String(16))  # ACQUISITION | INJECTION
    label: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    evidence_root: Mapped[str] = mapped_column(String(66))
    item_ids: Mapped[str] = mapped_column(Text)  # canonical JSON list
    failures: Mapped[str] = mapped_column(Text)  # canonical JSON list of AcquisitionFailure
    acquisition: Mapped[str] = mapped_column(Text)  # timeline, spend, timing


class EvidenceItemRow(Base):
    __tablename__ = "evidence_items"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # evidence_id
    contract_id: Mapped[str] = mapped_column(ForeignKey("evidence_contracts.id"), index=True)
    requirement_id: Mapped[str] = mapped_column(String(64))
    claim: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(128))
    mode: Mapped[str] = mapped_column(String(16))
    source_type: Mapped[str] = mapped_column(String(16))
    item_hash: Mapped[str] = mapped_column(String(66))
    cost: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    payload: Mapped[str] = mapped_column(Text)  # canonical EvidenceItem


class PaymentRow(Base):
    __tablename__ = "x402_payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evidence_set_id: Mapped[str] = mapped_column(String(64), index=True)
    contract_id: Mapped[str] = mapped_column(String(64), index=True)
    acquisition_id: Mapped[str] = mapped_column(String(64))
    evidence_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    requirement_id: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(128))
    protocol: Mapped[str] = mapped_column(String(16))
    network: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    scheme: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    quoted: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    settled: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    currency: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24))
    payment_reference: Mapped[Optional[str]] = mapped_column(String(132), nullable=True)
    payer: Mapped[Optional[str]] = mapped_column(String(42), nullable=True)
    pay_to: Mapped[Optional[str]] = mapped_column(String(42), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class DecisionRow(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("evidence_contracts.id"), index=True)
    evidence_set_id: Mapped[str] = mapped_column(ForeignKey("evidence_sets.id"))
    state: Mapped[str] = mapped_column(String(16), index=True)
    reason_codes: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(66), unique=True)
    contract_hash: Mapped[str] = mapped_column(String(66))
    evidence_root: Mapped[str] = mapped_column(String(66))
    engine_version: Mapped[str] = mapped_column(String(16))
    evaluated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    snapshot: Mapped[str] = mapped_column(Text)  # canonical decision inputs (replay source)
    payload: Mapped[str] = mapped_column(Text)  # canonical Decision


class VerificationResultRow(Base):
    __tablename__ = "verification_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), index=True)
    requirement_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    payload: Mapped[str] = mapped_column(Text)


class AuthorizationRow(Base):
    __tablename__ = "authorizations"
    id: Mapped[str] = mapped_column(String(66), primary_key=True)  # authorization_id (bytes32)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), unique=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    onchain_intent_id: Mapped[str] = mapped_column(String(66))
    contract_hash: Mapped[str] = mapped_column(String(66))
    evidence_root: Mapped[str] = mapped_column(String(66))
    action_hash: Mapped[str] = mapped_column(String(66))
    executor: Mapped[str] = mapped_column(String(42))
    nonce: Mapped[str] = mapped_column(String(66))
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    chain_id: Mapped[int] = mapped_column(BigInteger)
    gate_address: Mapped[str] = mapped_column(String(42))
    register_tx_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    authorize_tx_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    payload: Mapped[str] = mapped_column(Text)  # canonical Authorization
    action: Mapped[str] = mapped_column(Text)  # canonical CanonicalAction


class ExecutionRow(Base):
    __tablename__ = "executions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    authorization_id: Mapped[Optional[str]] = mapped_column(String(66), index=True, nullable=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    tx_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    block_number: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(96), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    payload: Mapped[str] = mapped_column(Text)  # canonical Execution


class ReceiptRow(Base):
    __tablename__ = "receipts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), index=True)
    execution_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(24))
    receipt_hash: Mapped[str] = mapped_column(String(66), unique=True)
    body: Mapped[str] = mapped_column(Text)  # canonical receipt body (hashed)
    commitment_status: Mapped[str] = mapped_column(String(16))  # NONE | CONFIRMED | FAILED | SUBMITTED
    commitment_tx_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    commitment_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)


class ChainTransactionRow(Base):
    __tablename__ = "chain_transactions"
    __table_args__ = (Index("ix_chain_tx_related", "related_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation_id: Mapped[str] = mapped_column(String(64), index=True)
    chain_id: Mapped[int] = mapped_column(BigInteger)
    purpose: Mapped[str] = mapped_column(String(32))
    related_id: Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    tx_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True, index=True)
    from_address: Mapped[str] = mapped_column(String(42))
    to_address: Mapped[Optional[str]] = mapped_column(String(42), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    block_number: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    gas_used: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    error_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    explorer_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ChainEventRow(Base):
    __tablename__ = "chain_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain_tx_id: Mapped[int] = mapped_column(ForeignKey("chain_transactions.id"), index=True)
    tx_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    event: Mapped[str] = mapped_column(String(64))
    log_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    args: Mapped[str] = mapped_column(Text)


class AuditEventRow(Base):
    """Append-only, hash-chained audit log (Document 03 §108-109)."""

    __tablename__ = "audit_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    subject_id: Mapped[str] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    payload: Mapped[str] = mapped_column(Text)
    prev_hash: Mapped[str] = mapped_column(String(66))
    event_hash: Mapped[str] = mapped_column(String(66), unique=True)
