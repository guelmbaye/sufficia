"""Machine reason codes.

Human explanations are *derived* from these codes plus structured values (Invariant A3,
Document 03 §35-36). No free text is ever the source of a decision.
"""

from __future__ import annotations

from enum import Enum


class ReasonCode(str, Enum):
    # --- decision-level (primary) ---------------------------------------------------------
    ALL_REQUIREMENTS_SATISFIED = "ALL_REQUIREMENTS_SATISFIED"
    MISSING_REQUIRED_EVIDENCE = "MISSING_REQUIRED_EVIDENCE"
    INSUFFICIENT_EVIDENCE_COUNT = "INSUFFICIENT_EVIDENCE_COUNT"
    EVIDENCE_BUDGET_EXHAUSTED = "EVIDENCE_BUDGET_EXHAUSTED"
    EVIDENCE_STALE = "EVIDENCE_STALE"
    CRITICAL_SOURCE_CONFLICT = "CRITICAL_SOURCE_CONFLICT"
    THRESHOLD_NOT_MET = "THRESHOLD_NOT_MET"
    PROHIBITED_EVIDENCE_PRESENT = "PROHIBITED_EVIDENCE_PRESENT"  # NOT_EXISTS requirement violated
    AUTHORITY_WINDOW_EXHAUSTED = "AUTHORITY_WINDOW_EXHAUSTED"  # evidence passes but expires before a usable window

    # --- evidence-level validation (causes) ------------------------------------------------
    INVALID_EVIDENCE_TYPE = "INVALID_EVIDENCE_TYPE"
    INVALID_VALUE = "INVALID_VALUE"  # NaN / Infinity / out of physical range
    INVALID_UNIT = "INVALID_UNIT"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
    PROVENANCE_MISSING = "PROVENANCE_MISSING"
    CLAIM_MISMATCH = "CLAIM_MISMATCH"
    UNKNOWN_REQUIREMENT = "UNKNOWN_REQUIREMENT"
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    SOURCE_NOT_PERMITTED = "SOURCE_NOT_PERMITTED"
    DUPLICATE_EVIDENCE = "DUPLICATE_EVIDENCE"
    EVIDENCE_ID_COLLISION = "EVIDENCE_ID_COLLISION"  # one evidence_id, different observations
    INVALID_COST = "INVALID_COST"  # negative / non-finite accounting record

    # --- acquisition failures (causes) ----------------------------------------------------
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    MISSING_FIELD = "MISSING_FIELD"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    SOURCE_NOT_CONFIGURED = "SOURCE_NOT_CONFIGURED"

    # --- authority / execution -------------------------------------------------------------
    EVIDENCE_AUTHORIZATION_REQUIRED = "EVIDENCE_AUTHORIZATION_REQUIRED"
    AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    ACTION_HASH_MISMATCH = "ACTION_HASH_MISMATCH"
    AUTHORIZATION_ALREADY_CONSUMED = "AUTHORIZATION_ALREADY_CONSUMED"
    AUTHORIZATION_NOT_FOUND = "AUTHORIZATION_NOT_FOUND"
    UNAUTHORIZED_EXECUTOR = "UNAUTHORIZED_EXECUTOR"
    AUTHORITY_EXPANSION = "AUTHORITY_EXPANSION"

    def __str__(self) -> str:  # pragma: no cover
        return self.value


# Evidence-level causes that make an item unusable.
INVALIDITY_CODES = frozenset(
    {
        ReasonCode.INVALID_EVIDENCE_TYPE,
        ReasonCode.INVALID_VALUE,
        ReasonCode.INVALID_UNIT,
        ReasonCode.INVALID_TIMESTAMP,
        ReasonCode.FUTURE_TIMESTAMP,
        ReasonCode.PROVENANCE_MISSING,
        ReasonCode.CLAIM_MISMATCH,
        ReasonCode.UNKNOWN_REQUIREMENT,
        ReasonCode.CONTRACT_MISMATCH,
        ReasonCode.SOURCE_NOT_PERMITTED,
        ReasonCode.EVIDENCE_ID_COLLISION,
        ReasonCode.INVALID_COST,
    }
)

ACQUISITION_CODES = frozenset(
    {
        ReasonCode.SOURCE_UNAVAILABLE,
        ReasonCode.PAYMENT_FAILED,
        ReasonCode.RATE_LIMITED,
        ReasonCode.INVALID_RESPONSE,
        ReasonCode.MISSING_FIELD,
        ReasonCode.BUDGET_EXCEEDED,
        ReasonCode.SOURCE_NOT_CONFIGURED,
        ReasonCode.INVALID_UNIT,
        ReasonCode.INVALID_TIMESTAMP,
        ReasonCode.PROVENANCE_MISSING,
    }
)
