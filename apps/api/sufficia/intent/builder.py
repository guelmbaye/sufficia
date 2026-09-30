"""ParsedIntent -> validated Evidence Contract.

The builder is where policy defaults enter (freshness, TTL, evidence budget, conflict tolerance,
evidence count) and where the compiler adds its only implicit requirement: a current price for
BUY / SELL, needed to bound slippage. Implicit requirements are always *restrictive* (they can only
remove authority) and are labelled ``origin="IMPLICIT"`` so the UI can show them as such.

Whatever comes out has passed :func:`~sufficia.decision_engine.validator.validate_contract`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..config import Settings
from ..decision_engine.validator import validate_contract
from ..domain.claims import get_claim
from ..domain.enums import ActionType, ConflictPolicy, Operator
from ..domain.models import (
    AuthorizationPolicy,
    ContractPolicy,
    EconomicLimits,
    EvidenceContract,
    IntentAction,
    Money,
    Requirement,
)
from .errors import IntentCompilationError
from .parser import INFLOW, LIQUIDITY, PRICE, plain
from .schema import USDC, ParsedCondition, ParsedIntent

REQUIREMENT_IDS = {
    INFLOW: "req_inflow",
    LIQUIDITY: "req_liquidity",
    PRICE: "req_price",
}
IMPLICIT = "IMPLICIT"
USER = "USER"
IMPLICIT_PRICE_NOTE = "Added by the compiler: a current price observation is needed to bound slippage."


def requirement_base_id(claim: str) -> str:
    return REQUIREMENT_IDS.get(claim, f"req_{claim}")


def effective_max_age(condition_age: int | None, global_age: int | None, default: int) -> int:
    """Per-condition age if stated, else the global evidence age, else the policy default.
    When both a per-condition and a global limit are stated, the stricter (smaller) one applies."""
    stated = [a for a in (condition_age, global_age) if a is not None]
    return min(stated) if stated else default


def check_amount(parsed: ParsedIntent, settings: Settings) -> None:
    """Mandatory parts, currency and the protected-amount cap (raises IntentCompilationError)."""
    missing = parsed.missing
    if missing:
        raise IntentCompilationError(missing=missing, unparsed=parsed.unparsed, code="INTENT_INCOMPLETE")
    amount = parsed.max_amount
    assert amount is not None
    if parsed.currency != USDC:
        raise IntentCompilationError(
            problems=[
                f"the maximum amount must be expressed in USDC (or USD / $), got '{plain(amount)} {parsed.currency}'"
                f" — e.g. 'up to 100 USDC of {parsed.asset or 'TOKEN-X'}'"
            ],
            code="UNSUPPORTED_CURRENCY",
        )
    if not amount.is_finite() or amount <= 0:
        raise IntentCompilationError(problems=["the maximum amount must be positive"], code="INVALID_AMOUNT")
    cap = Decimal(settings.max_protected_amount_usdc)
    if amount > cap:
        raise IntentCompilationError(
            problems=[f"the maximum amount {plain(amount)} USDC exceeds the protected-amount cap of {plain(cap)} USDC"],
            code="AMOUNT_CAP_EXCEEDED",
        )


def _requirement(
    cond: ParsedCondition,
    req_id: str,
    parsed: ParsedIntent,
    settings: Settings,
    *,
    origin: str,
) -> Requirement:
    spec = get_claim(cond.claim)
    tolerance = parsed.conflict_tolerance if parsed.conflict_tolerance is not None else settings.default_conflict_tolerance
    return Requirement(
        id=req_id,
        claim=cond.claim,
        operator=cond.operator,
        threshold=plain(cond.threshold) if cond.threshold is not None else None,
        values=list(cond.values) if cond.values is not None else None,
        expected=cond.expected,
        unit=spec.unit if spec is not None else None,
        required=True,
        max_age_seconds=effective_max_age(cond.max_age_seconds, parsed.max_age_seconds, settings.default_max_age_seconds),
        minimum_items=1,
        conflict_tolerance=plain(Decimal(tolerance)),
        conflict_policy=parsed.conflict_policy or ConflictPolicy.ESCALATE,
        origin=origin,
        label=spec.label if spec is not None else cond.claim,
    )


def build_requirements(parsed: ParsedIntent, settings: Settings) -> list[Requirement]:
    requirements: list[Requirement] = []
    used: dict[str, int] = {}
    for cond in parsed.conditions:
        base = requirement_base_id(cond.claim)
        used[base] = used.get(base, 0) + 1
        req_id = base if used[base] == 1 else f"{base}_{used[base]}"
        origin = cond.origin if cond.origin in (USER, IMPLICIT) else USER
        requirements.append(_requirement(cond, req_id, parsed, settings, origin=origin))
    has_price = any(c.claim == PRICE for c in parsed.conditions)
    if parsed.action in (ActionType.BUY, ActionType.SELL) and not has_price:
        base = requirement_base_id(PRICE)
        req_id = base if base not in used else f"{base}_{used[base] + 1}"
        implicit = ParsedCondition(claim=PRICE, operator=Operator.EXISTS, origin=IMPLICIT)
        req = _requirement(implicit, req_id, parsed, settings, origin=IMPLICIT)
        requirements.append(req)
    return requirements


def assemble_contract(parsed: ParsedIntent, *, intent_id: str, contract_id: str, settings: Settings) -> EvidenceContract:
    """Applies policy defaults to a parsed instruction *without* validating it or enforcing the amount
    cap / currency. Used to obtain the reference contract that user edits are compared with.
    Requires action, asset and maximum amount (raises IntentCompilationError otherwise)."""
    if parsed.missing:
        raise IntentCompilationError(missing=parsed.missing, unparsed=parsed.unparsed, code="INTENT_INCOMPLETE")
    assert parsed.action is not None and parsed.asset is not None and parsed.max_amount is not None
    requirements = build_requirements(parsed, settings)
    required_count = sum(1 for r in requirements if r.required)
    default_minimum = min(settings.default_minimum_required_evidence, required_count)
    minimum = max(parsed.minimum_required_evidence or 0, default_minimum)
    budget = parsed.evidence_budget_usdc if parsed.evidence_budget_usdc is not None else settings.default_evidence_budget_usdc
    amount = plain(parsed.max_amount)
    return EvidenceContract(
        contract_id=contract_id,
        intent_id=intent_id,
        action=IntentAction(
            type=parsed.action, asset=parsed.asset, max_amount=Money(value=amount, currency=parsed.currency or USDC)
        ),
        requirements=requirements,
        policy=ContractPolicy(minimum_required_evidence=minimum),
        economic_limits=EconomicLimits(max_execution_value_usd=amount, max_evidence_cost_usd=plain(Decimal(budget))),
        authorization=AuthorizationPolicy(ttl_seconds=settings.default_authorization_ttl_seconds),
    )


def build_contract(parsed: ParsedIntent, *, intent_id: str, contract_id: str, settings: Settings) -> EvidenceContract:
    """Builds and validates the Evidence Contract for a parsed instruction.

    Defaults: ``max_age_seconds`` per requirement = stated per-condition age, else the global
    evidence age, else ``settings.default_max_age_seconds`` (the stricter of the two when both are
    stated); TTL, evidence budget and conflict tolerance from settings unless stated;
    ``minimum_required_evidence = max(stated, min(settings.default_minimum_required_evidence,
    number of required requirements))``; ``economic_limits.max_execution_value_usd`` = the maximum
    amount. For BUY / SELL without a price condition an IMPLICIT ``req_price`` (asset_price EXISTS)
    is added.

    Raises :class:`IntentCompilationError` when mandatory parts are missing, the amount is not in
    USDC, is not positive or exceeds ``settings.max_protected_amount_usdc``, or when there is no
    evidence condition at all (TRANSFER without conditions); propagates
    :class:`~sufficia.decision_engine.validator.InvalidContractError` for any other contract problem
    (unknown claim, operator not allowed for the claim, ...).
    """
    check_amount(parsed, settings)
    contract = assemble_contract(parsed, intent_id=intent_id, contract_id=contract_id, settings=settings)
    if not contract.requirements:
        raise IntentCompilationError(
            problems=[
                "no evidence condition was found; add at least one condition such as "
                "'only if liquidity exceeds $500K'"
            ],
            unparsed=parsed.unparsed,
            code="NO_CONDITIONS",
        )
    validate_contract(contract)
    return contract


def enforce_limits(contract: EvidenceContract, settings: Settings) -> None:
    """Server-side hard limits for any contract, including user-edited ones."""
    money = contract.action.max_amount
    if money.currency != USDC:
        raise IntentCompilationError(
            problems=[f"the maximum amount must be in USDC, got {money.currency}"], code="UNSUPPORTED_CURRENCY"
        )
    cap = Decimal(settings.max_protected_amount_usdc)
    if money.value > cap:
        raise IntentCompilationError(
            problems=[f"the maximum amount {plain(money.value)} USDC exceeds the protected-amount cap of {plain(cap)} USDC"],
            code="AMOUNT_CAP_EXCEEDED",
        )
    if contract.economic_limits.max_execution_value_usd > cap:
        raise IntentCompilationError(
            problems=[f"economic_limits.max_execution_value_usd exceeds the protected-amount cap of {plain(cap)} USDC"],
            code="AMOUNT_CAP_EXCEEDED",
        )


def contract_from_dict(data: dict[str, Any], *, intent_id: str, contract_id: str) -> EvidenceContract:
    """Parses a user-edited contract (JSON) and binds it to this intent / contract id."""
    payload = dict(data)
    payload["intent_id"] = intent_id
    payload["contract_id"] = contract_id
    return EvidenceContract.model_validate(payload)


__all__ = [
    "IMPLICIT",
    "IMPLICIT_PRICE_NOTE",
    "REQUIREMENT_IDS",
    "USER",
    "assemble_contract",
    "build_contract",
    "build_requirements",
    "check_amount",
    "contract_from_dict",
    "effective_max_age",
    "enforce_limits",
    "requirement_base_id",
]
