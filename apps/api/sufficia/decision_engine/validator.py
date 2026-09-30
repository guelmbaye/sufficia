"""Contract and evidence validation (Document 03 §15-16, §83-85)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from ..canonical.canonicalize import decimal_shape
from ..domain.claims import MAX_ABS_VALUE, get_claim
from ..domain.enums import DecisionState, Operator, SourceType
from ..domain.models import EvidenceContract, EvidenceItem
from ..domain.reasons import ReasonCode

HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
MAX_TTL_SECONDS = 600  # EvidenceGate.MAX_AUTHORIZATION_TTL
MAX_AGE_SECONDS = 86_400

NUMERIC_OPERATORS = {Operator.GT, Operator.GTE, Operator.LT, Operator.LTE}
# Numbers are exact Decimals. Bounding magnitude (claims.MAX_ABS_VALUE) and fractional precision keeps
# every engine subtraction / product exact inside the engine's 200-digit arithmetic context.
MAX_FRACTION_DIGITS = 30


def within_precision(d: Decimal) -> bool:
    if not d.is_finite():
        return False
    if d.copy_abs() > MAX_ABS_VALUE:  # copy_abs: no context arithmetic (Emax overflow)
        return False
    _, digits, exp = decimal_shape(d)
    return digits == "0" or exp >= -MAX_FRACTION_DIGITS


class InvalidContractError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def validate_contract(contract: EvidenceContract) -> None:
    """Rejects contracts that cannot be evaluated deterministically or that could create authority
    from a failure (a failure policy mapped to AUTHORIZE)."""
    problems: list[str] = []
    if not contract.requirements:
        problems.append("at least one requirement is required")
    ids = [r.id for r in contract.requirements]
    if len(ids) != len(set(ids)):
        problems.append("requirement ids must be unique")
    for r in contract.requirements:
        spec = get_claim(r.claim)
        if spec is None:
            problems.append(f"{r.id}: unknown claim '{r.claim}'")
            continue
        if r.operator not in spec.allowed_operators:
            problems.append(f"{r.id}: operator {r.operator.value} not allowed for claim {r.claim}")
        if r.unit is not None and r.unit != spec.unit:
            problems.append(f"{r.id}: unit must be {spec.unit}")
        if r.operator in NUMERIC_OPERATORS or (r.operator in (Operator.EQ, Operator.NEQ) and spec.numeric):
            if r.threshold is None or not r.threshold.is_finite():
                problems.append(f"{r.id}: a finite threshold is required for {r.operator.value}")
            elif not within_precision(r.threshold):
                problems.append(f"{r.id}: threshold magnitude/precision out of range")
        if r.operator == Operator.IN and not r.values:
            problems.append(f"{r.id}: IN requires a non-empty value list")
        if r.operator in (Operator.EQ, Operator.NEQ) and not spec.numeric and r.expected is None:
            problems.append(f"{r.id}: an expected value is required")
        if not 1 <= r.max_age_seconds <= MAX_AGE_SECONDS:
            problems.append(f"{r.id}: max_age_seconds must be within 1..{MAX_AGE_SECONDS}")
        if r.minimum_items < 1:
            problems.append(f"{r.id}: minimum_items must be >= 1")
        if not r.conflict_tolerance.is_finite() or not (Decimal(0) <= r.conflict_tolerance <= Decimal(1)):
            problems.append(f"{r.id}: conflict_tolerance must be within [0, 1]")
    p = contract.policy
    for name in ("missing_required", "stale_required", "critical_conflict", "failed_threshold"):
        if getattr(p, name) == DecisionState.AUTHORIZE:
            problems.append(f"policy.{name} cannot be AUTHORIZE (a failure can never create authority)")
    if p.minimum_required_evidence < 0:
        problems.append("policy.minimum_required_evidence must be >= 0")
    if not 0 <= p.clock_skew_seconds <= 30:
        problems.append("policy.clock_skew_seconds must be within 0..30")
    amount = contract.action.max_amount.value
    if not amount.is_finite() or amount <= 0:
        problems.append("action.max_amount must be positive")
    elif amount > contract.economic_limits.max_execution_value_usd:
        problems.append("action.max_amount exceeds economic_limits.max_execution_value_usd")
    if contract.economic_limits.max_evidence_cost_usd < 0:
        problems.append("economic_limits.max_evidence_cost_usd must be >= 0")
    if not 1 <= contract.authorization.ttl_seconds <= MAX_TTL_SECONDS:
        problems.append(f"authorization.ttl_seconds must be within 1..{MAX_TTL_SECONDS}")
    if problems:
        raise InvalidContractError(problems)


@dataclass
class ItemValidation:
    codes: list[ReasonCode] = field(default_factory=list)
    value: Any = None  # normalized value (Decimal / bool / str) when valid
    observed_at: datetime | None = None

    @property
    def valid(self) -> bool:
        return not self.codes


def normalize_numeric(value: Any) -> tuple[Decimal | None, ReasonCode | None]:
    if isinstance(value, bool) or value is None:
        return None, ReasonCode.INVALID_EVIDENCE_TYPE
    if isinstance(value, int):
        d = Decimal(value)  # exact; not subject to the int->str digit limit
    elif isinstance(value, Decimal):
        d = value
    elif isinstance(value, float):
        if not math.isfinite(value):
            return None, ReasonCode.INVALID_VALUE
        try:
            d = Decimal(repr(value))
        except InvalidOperation:  # pragma: no cover
            return None, ReasonCode.INVALID_VALUE
    else:
        return None, ReasonCode.INVALID_EVIDENCE_TYPE
    if not within_precision(d):
        return None, ReasonCode.INVALID_VALUE
    return d, None


def validate_item(
    contract: EvidenceContract, item: EvidenceItem, evaluation_time: datetime, permitted: dict[str, set[str]] | None
) -> ItemValidation:
    result = ItemValidation()
    codes = result.codes

    if item.contract_id != contract.contract_id:
        codes.append(ReasonCode.CONTRACT_MISMATCH)
    requirement = contract.requirement(item.requirement_id)
    if requirement is None:
        codes.append(ReasonCode.UNKNOWN_REQUIREMENT)
        return result
    if item.claim.name != requirement.claim:
        codes.append(ReasonCode.CLAIM_MISMATCH)
    spec = get_claim(requirement.claim)
    assert spec is not None  # guaranteed by validate_contract

    if permitted is not None:
        allowed = permitted.get(item.source.provider)
        if allowed is None or requirement.claim not in allowed:
            codes.append(ReasonCode.SOURCE_NOT_PERMITTED)

    # value / type
    if spec.numeric:
        value, err = normalize_numeric(item.claim.value)
        if err:
            codes.append(err)
        elif value is not None:
            if (spec.min_value is not None and value < spec.min_value) or (
                spec.max_value is not None and value > spec.max_value
            ):
                codes.append(ReasonCode.INVALID_VALUE)
            else:
                result.value = value
    elif spec.unit == "BOOL":
        if isinstance(item.claim.value, bool):
            result.value = item.claim.value
        else:
            codes.append(ReasonCode.INVALID_EVIDENCE_TYPE)
    else:
        if isinstance(item.claim.value, str) and item.claim.value.strip():
            result.value = item.claim.value.strip()
        else:
            codes.append(ReasonCode.INVALID_EVIDENCE_TYPE)

    # unit
    if item.claim.unit != spec.unit:
        codes.append(ReasonCode.INVALID_UNIT)

    # timestamp
    observed = item.temporal.observed_at
    if not isinstance(observed, datetime) or observed.tzinfo is None:
        codes.append(ReasonCode.INVALID_TIMESTAMP)
    else:
        if observed > evaluation_time + timedelta(seconds=contract.policy.clock_skew_seconds):
            codes.append(ReasonCode.FUTURE_TIMESTAMP)
        else:
            result.observed_at = observed

    # provenance
    if not _has_provenance(item):
        codes.append(ReasonCode.PROVENANCE_MISSING)

    return result


def _has_provenance(item: EvidenceItem) -> bool:
    if not item.source.provider:
        return False
    integ = item.integrity
    response_ok = bool(integ.response_hash and HASH_RE.match(integ.response_hash))
    request_ok = bool(integ.request_hash and HASH_RE.match(integ.request_hash))
    st = item.source.source_type
    if st == SourceType.API:
        return response_ok and request_ok
    if st in (SourceType.ONCHAIN, SourceType.ORACLE):
        return response_ok and (request_ok or integ.chain_ref is not None)
    if st == SourceType.DERIVED:
        return response_ok and item.derivation is not None and len(item.derivation.inputs) > 0
    return False  # pragma: no cover
