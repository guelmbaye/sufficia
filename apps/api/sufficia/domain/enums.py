"""Closed vocabularies of the authority domain.

Every state that can influence authority is an explicit enum: no stringly-typed states flow
through the engine (Document 03 §111-115).
"""

from __future__ import annotations

from enum import Enum


class _StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class DecisionState(_StrEnum):
    """The five semantic outcomes of the Decision Gate (Document 02 §9)."""

    AUTHORIZE = "AUTHORIZE"  # every mandatory requirement passes
    DENY = "DENY"  # valid evidence explicitly fails a mandatory condition
    ABSTAIN = "ABSTAIN"  # required evidence cannot be established
    REFRESH = "REFRESH"  # required evidence exists but is stale
    ESCALATE = "ESCALATE"  # valid sources materially disagree


class RequirementStatus(_StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class EvidenceStatus(_StrEnum):
    VALID = "VALID"
    STALE = "STALE"
    INVALID = "INVALID"
    CONFLICTED = "CONFLICTED"
    DUPLICATE = "DUPLICATE"
    MISSING = "MISSING"


class Operator(_StrEnum):
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    EQ = "=="
    NEQ = "!="
    EXISTS = "EXISTS"
    NOT_EXISTS = "NOT_EXISTS"
    IN = "IN"


class ActionType(_StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    TRANSFER = "TRANSFER"


class SourceType(_StrEnum):
    API = "API"  # E1 external API evidence
    ONCHAIN = "ONCHAIN"  # E2 onchain evidence
    ORACLE = "ORACLE"  # E3 oracle evidence
    DERIVED = "DERIVED"  # E4 derived evidence (must keep lineage)


class SourceMode(_StrEnum):
    """How an observation was produced. Never blurred in the UI (Document 05 §58)."""

    LIVE = "LIVE"  # real external provider called now
    RECORDED = "RECORDED"  # genuine previously captured response, re-served for resilience
    SIMULATED = "SIMULATED"  # local reference service (e.g. local x402 reference server, local EVM pool)
    INJECTED = "INJECTED"  # Demo Lab failure-injection test condition


class ConflictPolicy(_StrEnum):
    ESCALATE = "ESCALATE"
    ABSTAIN = "ABSTAIN"


class ExecutionStatus(_StrEnum):
    BLOCKED = "BLOCKED"  # no authority: the action never reached the chain
    SUBMITTED = "SUBMITTED"  # transaction sent, not yet confirmed
    EXECUTED = "EXECUTED"  # confirmed on chain
    FAILED = "FAILED"  # authorized, but the transaction failed / reverted


class AuthorizationStatus(_StrEnum):
    ISSUED = "ISSUED"  # created offchain from an AUTHORIZE decision, not yet committed
    ACTIVE = "ACTIVE"  # committed onchain, unconsumed, unexpired
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    PUBLISH_FAILED = "PUBLISH_FAILED"


class PaymentStatus(_StrEnum):
    REQUESTED = "REQUESTED"
    QUOTED = "QUOTED"
    AUTHORIZED = "AUTHORIZED"
    VERIFIED = "VERIFIED"
    SETTLED = "SETTLED"
    SIMULATED = "SIMULATED"
    REJECTED = "REJECTED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    ALLOWANCE_REQUIRED = "ALLOWANCE_REQUIRED"
    SETTLEMENT_FAILED = "SETTLEMENT_FAILED"
    TIMEOUT = "TIMEOUT"


class TxPurpose(_StrEnum):
    INTENT_REGISTER = "INTENT_REGISTER"
    AUTHORIZATION = "AUTHORIZATION"
    PROTECTED_EXECUTION = "PROTECTED_EXECUTION"
    RECEIPT_COMMITMENT = "RECEIPT_COMMITMENT"
    EVIDENCE_PAYMENT = "EVIDENCE_PAYMENT"
    DEPLOYMENT = "DEPLOYMENT"


class TxStatus(_StrEnum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    REVERTED = "REVERTED"
    FAILED = "FAILED"  # could not be submitted


class ChainMode(_StrEnum):
    LOCAL_EVM = "LOCAL_EVM"  # in-process py-evm chain running the real EvidenceGate bytecode
    MONAD_TESTNET = "MONAD_TESTNET"
    MONAD_MAINNET = "MONAD_MAINNET"
    DISABLED = "DISABLED"


class CompilerKind(_StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    LLM = "LLM"
    OVERRIDE = "OVERRIDE"
