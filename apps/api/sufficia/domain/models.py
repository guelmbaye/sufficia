"""Typed domain objects of the authority model (Document 03 §111).

Raw dictionaries never flow through the engine. Every monetary / threshold value is a Decimal.
Evidence values are deliberately typed ``Any``: the *engine* (not the parser) decides whether a
value is usable, so malformed observations stay visible as INVALID evidence instead of silently
disappearing at the schema boundary.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    ActionType,
    AuthorizationStatus,
    CompilerKind,
    ConflictPolicy,
    DecisionState,
    EvidenceStatus,
    ExecutionStatus,
    Operator,
    PaymentStatus,
    RequirementStatus,
    SourceMode,
    SourceType,
)
from .reasons import ReasonCode

SCHEMA_VERSION = 1
POLICY_VERSION = 1


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, use_enum_values=False)


# =========================================================================================
# Intent & Evidence Contract
# =========================================================================================


class Money(Model):
    value: Decimal
    currency: str = "USDC"


class IntentAction(Model):
    type: ActionType
    asset: str
    max_amount: Money

    @field_validator("asset")
    @classmethod
    def _asset(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("asset is required")
        return v


class Requirement(Model):
    id: str
    claim: str
    operator: Operator
    threshold: Optional[Decimal] = None
    values: Optional[list[str]] = None  # for IN
    expected: Optional[bool | str] = None  # for boolean / identifier equality
    unit: Optional[str] = None
    required: bool = True
    max_age_seconds: int = 30
    minimum_items: int = 1
    conflict_tolerance: Decimal = Decimal("0.10")
    conflict_policy: ConflictPolicy = ConflictPolicy.ESCALATE
    origin: str = "USER"  # USER | IMPLICIT (added by the compiler, always restrictive)
    label: Optional[str] = None


class ContractPolicy(Model):
    minimum_required_evidence: int = 3
    missing_required: DecisionState = DecisionState.ABSTAIN
    stale_required: DecisionState = DecisionState.REFRESH
    critical_conflict: DecisionState = DecisionState.ESCALATE
    failed_threshold: DecisionState = DecisionState.DENY
    clock_skew_seconds: int = 2


class EconomicLimits(Model):
    max_execution_value_usd: Decimal
    max_evidence_cost_usd: Decimal = Decimal("0.25")


class AuthorizationPolicy(Model):
    ttl_seconds: int = 30


class EvidenceContract(Model):
    contract_id: str
    intent_id: str
    schema_version: int = SCHEMA_VERSION
    policy_version: int = POLICY_VERSION
    action: IntentAction
    requirements: list[Requirement]
    policy: ContractPolicy = Field(default_factory=ContractPolicy)
    economic_limits: EconomicLimits
    authorization: AuthorizationPolicy = Field(default_factory=AuthorizationPolicy)

    def requirement(self, requirement_id: str) -> Requirement | None:
        for r in self.requirements:
            if r.id == requirement_id:
                return r
        return None

    @property
    def required_requirements(self) -> list[Requirement]:
        return [r for r in self.requirements if r.required]


class Intent(Model):
    intent_id: str
    text: str
    created_at: datetime
    network: str = "monad"
    protected_amount: Optional[Money] = None
    evidence_budget: Optional[Money] = None


# =========================================================================================
# Evidence
# =========================================================================================


class EvidenceClaim(Model):
    name: str
    value: Any = None
    unit: Optional[str] = None


_ISO_TZ = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$")


class EvidenceTemporal(Model):
    """Timestamps are datetimes only when they are *unambiguous*: a datetime object, or an ISO-8601 string
    with an explicit offset. Anything else ("1700000000", "yesterday", 1700000000) is kept raw so the
    engine flags INVALID_TIMESTAMP — and a replay re-parses it to exactly the same thing (no lenient
    coercion that could turn an invalid observation into a valid one after a database round trip)."""

    observed_at: Optional[datetime | str] = None
    received_at: Optional[datetime | str] = None

    @field_validator("observed_at", "received_at", mode="before")
    @classmethod
    def _strict_timestamp(cls, v: Any) -> Any:
        if v is None or isinstance(v, datetime):
            return v
        if isinstance(v, str):
            if _ISO_TZ.match(v):
                try:
                    return datetime.fromisoformat(v.replace("Z", "+00:00"))
                except ValueError:
                    return v
            return v
        return repr(v)


class EvidenceSource(Model):
    provider: str
    source_type: SourceType
    mode: SourceMode
    endpoint_id: Optional[str] = None
    upstream_source: Optional[str] = None  # e.g. "chainlink:TOKENX/USD" when an API relays an oracle
    source_family: Optional[str] = None  # independence group; unknown => not claimed independent
    network: Optional[str] = None
    captured_at: Optional[datetime] = None  # RECORDED fixtures keep their original capture time


class ChainReference(Model):
    chain_id: int
    block_number: Optional[int] = None
    block_hash: Optional[str] = None
    tx_hash: Optional[str] = None
    contract: Optional[str] = None


class EvidenceIntegrity(Model):
    request_hash: Optional[str] = None
    response_hash: Optional[str] = None
    chain_ref: Optional[ChainReference] = None


class EvidencePayment(Model):
    protocol: str = "x402"
    network: Optional[str] = None
    scheme: Optional[str] = None
    quoted_amount: Optional[Decimal] = None
    settled_amount: Optional[Decimal] = None
    currency: str = "USDC"
    payment_reference: Optional[str] = None
    status: PaymentStatus = PaymentStatus.REQUESTED
    payer: Optional[str] = None
    pay_to: Optional[str] = None


class EvidenceEconomic(Model):
    cost: Decimal = Decimal(0)
    currency: str = "USDC"
    payment: Optional[EvidencePayment] = None


class EvidenceDerivation(Model):
    method: str
    version: str
    inputs: list[str]


class EvidenceItem(Model):
    evidence_id: str
    contract_id: str
    requirement_id: str
    claim: EvidenceClaim
    temporal: EvidenceTemporal
    source: EvidenceSource
    integrity: EvidenceIntegrity = Field(default_factory=EvidenceIntegrity)
    economic: EvidenceEconomic = Field(default_factory=EvidenceEconomic)
    derivation: Optional[EvidenceDerivation] = None
    acquisition_id: Optional[str] = None


class AcquisitionFailure(Model):
    requirement_id: str
    provider: str
    code: ReasonCode
    detail: str = ""
    cost_spent: Decimal = Decimal(0)
    acquisition_id: Optional[str] = None


class EvidenceSet(Model):
    contract_id: str
    items: list[EvidenceItem] = Field(default_factory=list)
    acquisition_failures: list[AcquisitionFailure] = Field(default_factory=list)


# =========================================================================================
# Engine outputs
# =========================================================================================


class Reason(Model):
    code: ReasonCode
    requirement_id: Optional[str] = None
    claim: Optional[str] = None
    observed: Optional[list[str]] = None
    required: Optional[str] = None
    causes: list[ReasonCode] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class EvidenceAssessment(Model):
    evidence_id: str
    requirement_id: str
    status: EvidenceStatus
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    age_seconds: Optional[Decimal] = None
    expires_at: Optional[datetime] = None
    normalized_value: Optional[str] = None
    item_hash: Optional[str] = None


class RequirementChecks(Model):
    present: bool
    fresh: bool
    valid_type: bool
    provenance: bool
    conflict: bool
    threshold: Optional[bool]


class RequirementResult(Model):
    requirement_id: str
    claim: str
    operator: Operator
    required_expression: str
    required: bool
    status: RequirementStatus
    evidence_ids: list[str] = Field(default_factory=list)
    observed_values: list[str] = Field(default_factory=list)
    checks: RequirementChecks
    reason_code: Optional[ReasonCode] = None
    human_reason: str = ""


class AuthorityWindow(Model):
    issued_at: datetime
    expires_at: datetime
    ttl_seconds: Decimal
    bounded_by: str  # "CONTRACT_TTL" | "EVIDENCE_EXPIRY:<evidence_id>"


class CanonicalAction(Model):
    """The exact economic action an authorization permits (Document 04 §17)."""

    action_type: ActionType
    asset: str
    amount: Money
    chain_id: int
    gate_address: str
    target: str
    value: int = 0
    data: str = "0x"
    asset_in: str
    max_amount_in: int
    asset_out: str
    min_amount_out: int
    recipient: str
    deadline: int
    nonce: str
    adapter: str
    description: str = ""
    # Raw units of `amount.currency` the action can move out of the gate (approval for swaps, transfer
    # amount for transfers) and their decimals. build_authorization bounds it by the contract maximum
    # (Invariant A1); the execution adapter proves it against the calldata.
    amount_decimals: int = 6
    spend_raw: Optional[int] = None


class Authorization(Model):
    authorization_id: str
    decision_id: str
    intent_id: str
    onchain_intent_id: str
    contract_hash: str
    evidence_root: str
    action_hash: str
    executor: str
    nonce: str
    issued_at: datetime
    expires_at: datetime
    max_amount: Money
    asset: str
    status: AuthorizationStatus = AuthorizationStatus.ISSUED


class Decision(Model):
    decision_id: str
    contract_id: str
    intent_id: str
    state: DecisionState
    reason_codes: list[ReasonCode]
    reasons: list[Reason]
    headline: str
    summary: str
    requirement_results: list[RequirementResult]
    evidence_assessments: list[EvidenceAssessment]
    evidence_ids: list[str]
    evidence_count: int
    independent_source_count: Optional[int]
    independence_known: bool
    coverage: Decimal
    freshness_pass: bool
    conflicts: int
    evidence_cost_usd: Decimal
    contract_hash: str
    evidence_root: str
    engine_version: str
    evaluated_at: datetime
    fingerprint: str
    authority_window: Optional[AuthorityWindow] = None

    @model_validator(mode="after")
    def _window_only_when_authorized(self) -> "Decision":
        if self.state != DecisionState.AUTHORIZE and self.authority_window is not None:
            raise ValueError("INV-01: an authority window can only exist for AUTHORIZE")
        if self.state == DecisionState.AUTHORIZE and self.authority_window is None:
            raise ValueError("AUTHORIZE decisions must carry an authority window")
        return self


# =========================================================================================
# Execution & receipts
# =========================================================================================


class Execution(Model):
    execution_id: str
    authorization_id: str
    status: ExecutionStatus
    network: str
    chain_id: int
    tx_hash: Optional[str] = None
    block_number: Optional[int] = None
    error_code: Optional[str] = None
    error: Optional[str] = None
    submitted_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    amount: Optional[Money] = None


class ReceiptEvidenceLine(Model):
    requirement_id: str
    claim: str
    required: str
    observed: list[str]
    status: RequirementStatus
    providers: list[str]
    modes: list[SourceMode]


class ReceiptPaymentLine(Model):
    evidence_id: Optional[str]
    provider: str
    amount: Optional[Decimal]
    currency: str
    status: PaymentStatus
    payment_reference: Optional[str]
    network: Optional[str]


class EvidenceReceipt(Model):
    receipt_id: str
    kind: str  # BLOCKED | EXECUTED | FAILED | EXPIRED | AUTHORIZED_PENDING
    intent_id: str
    intent_text: str
    intent_hash: str
    contract_id: str
    contract_hash: str
    evidence_root: str
    action_hash: Optional[str]
    decision_id: str
    decision_state: DecisionState
    reason_codes: list[ReasonCode]
    decision_reason: str
    engine_version: str
    evaluated_at: datetime
    evidence_count: int
    evidence_cost_usdc: Decimal
    coverage: Decimal
    freshness_pass: bool
    conflicts: int
    evidence: list[ReceiptEvidenceLine]
    payments: list[ReceiptPaymentLine]
    authorization: Optional[dict[str, Any]]
    execution: dict[str, Any]
    protected_amount: Money
    network: str
    chain_mode: str
    created_at: datetime
    receipt_hash: Optional[str] = None


class CompiledContract(Model):
    contract: EvidenceContract
    contract_hash: str
    compiler: CompilerKind
    preservation: dict[str, Any]
    summary: dict[str, Any]
