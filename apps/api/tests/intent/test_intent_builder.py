"""Builder: ParsedIntent -> validated EvidenceContract (defaults, implicit price, ids, caps, validation)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from intent_helpers import GRAND_PRIZE, make_settings, requirement_view
from sufficia.decision_engine.validator import InvalidContractError, validate_contract
from sufficia.domain.enums import ActionType, ConflictPolicy, DecisionState, Operator
from sufficia.intent import IntentCompilationError, ParsedCondition, ParsedIntent, build_contract, enforce_limits, parse_intent

IN, LIQ, PRICE = "smart_money_inflow_24h", "available_liquidity", "asset_price"


def build(text: str, settings=None):
    return build_contract(parse_intent(text), intent_id="intent_1", contract_id="contract_1", settings=settings or make_settings())


def test_grand_prize_contract(settings):
    c = build(GRAND_PRIZE, settings)
    validate_contract(c)
    assert (c.contract_id, c.intent_id) == ("contract_1", "intent_1")
    assert (c.action.type, c.action.asset, c.action.max_amount.value, c.action.max_amount.currency) == (
        ActionType.BUY, "TOKEN-X", Decimal("100"), "USDC",
    )
    assert requirement_view(c) == [
        ("req_inflow", IN, ">", "1000000", 30, "USER"),
        ("req_liquidity", LIQ, ">", "500000", 30, "USER"),
        ("req_price", PRICE, "EXISTS", None, 30, "IMPLICIT"),
    ]
    for r in c.requirements:
        assert r.required and r.unit == "USD" and r.minimum_items == 1
        assert r.conflict_tolerance == Decimal("0.1")
        assert r.conflict_policy == ConflictPolicy.ESCALATE
        assert r.label
    assert c.policy.minimum_required_evidence == 3
    assert (c.policy.missing_required, c.policy.stale_required, c.policy.critical_conflict, c.policy.failed_threshold) == (
        DecisionState.ABSTAIN, DecisionState.REFRESH, DecisionState.ESCALATE, DecisionState.DENY,
    )
    assert c.economic_limits.max_execution_value_usd == Decimal("100")
    assert c.economic_limits.max_evidence_cost_usd == Decimal("0.25")
    assert c.authorization.ttl_seconds == 30


def test_implicit_price_added_for_buy_and_sell_only_without_price_condition():
    assert requirement_view(build("Sell up to 50 USDC of MON if liquidity exceeds $500K"))[-1] == (
        "req_price", PRICE, "EXISTS", None, 30, "IMPLICIT",
    )
    with_price = build("Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K and price is below $0.50")
    assert [r.id for r in with_price.requirements] == ["req_liquidity", "req_price"]
    assert with_price.requirement("req_price").origin == "USER"
    assert with_price.requirement("req_price").operator == Operator.LT
    transfer = build("Transfer 25 USDC if liquidity exceeds $500K")
    assert [r.id for r in transfer.requirements] == ["req_liquidity"]


def test_implicit_price_only_contract_for_unconditional_buy():
    c = build("Buy up to 100 USDC of TOKEN-X")
    assert requirement_view(c) == [("req_price", PRICE, "EXISTS", None, 30, "IMPLICIT")]
    assert c.policy.minimum_required_evidence == 1


def test_repeated_claims_get_suffixed_ids():
    c = build("Buy up to 100 USDC of TOKEN-X if price is between $0.40 and $0.60 and liquidity exceeds $500K and liquidity is below $5M")
    assert [r.id for r in c.requirements] == ["req_price", "req_price_2", "req_liquidity", "req_liquidity_2"]
    assert [(r.operator.value, str(r.threshold)) for r in c.requirements] == [
        (">=", "0.4"), ("<=", "0.6"), (">", "500000"), ("<", "5000000"),
    ]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K", 2),  # liquidity + implicit price
        (GRAND_PRIZE, 3),
        ("Transfer 25 USDC if liquidity exceeds $500K", 1),
        ("Buy up to 100 USDC of TOKEN-X if price is between $0.4 and $0.6 and liquidity > $1M and inflows > $1M", 3),
        ("Transfer 25 USDC if liquidity exceeds $500K, at least 3 sources", 3),  # stated minimum is kept
        ("Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K, at least 1 source", 2),  # never below the default
    ],
)
def test_minimum_required_evidence(text, expected):
    assert build(text).policy.minimum_required_evidence == expected


def test_minimum_required_evidence_follows_settings_default():
    c = build(GRAND_PRIZE, make_settings(default_minimum_required_evidence=2))
    assert c.policy.minimum_required_evidence == 2


def test_stated_global_age_applies_to_every_requirement_including_implicit():
    c = build("Buy up to 100 USDC of TOKEN-X if inflows exceed $1M and liquidity exceeds $500K, evidence under 15 seconds")
    assert {r.max_age_seconds for r in c.requirements} == {15}


def test_per_condition_age_and_stricter_of_both():
    c = build("Buy up to 100 USDC of TOKEN-X if inflows exceed $1M (max age 10s) and liquidity exceeds $500K")
    assert [(r.id, r.max_age_seconds) for r in c.requirements] == [("req_inflow", 10), ("req_liquidity", 30), ("req_price", 30)]
    c2 = build("Buy up to 100 USDC of TOKEN-X if inflows exceed $1M (max age 45s), evidence under 20 seconds")
    assert c2.requirement("req_inflow").max_age_seconds == 20


def test_defaults_follow_settings():
    s = make_settings(
        default_max_age_seconds=12,
        default_authorization_ttl_seconds=9,
        default_evidence_budget_usdc=Decimal("0.05"),
        default_conflict_tolerance=Decimal("0.02"),
    )
    c = build(GRAND_PRIZE, s)
    assert {r.max_age_seconds for r in c.requirements} == {12}
    assert {r.conflict_tolerance for r in c.requirements} == {Decimal("0.02")}
    assert c.authorization.ttl_seconds == 9
    assert c.economic_limits.max_evidence_cost_usd == Decimal("0.05")


def test_stated_policy_overrides_defaults():
    c = build(
        "Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K, abstain on conflict, sources agree within 5%, evidence budget $0.10"
    )
    assert {r.conflict_policy for r in c.requirements} == {ConflictPolicy.ABSTAIN}
    assert {r.conflict_tolerance for r in c.requirements} == {Decimal("0.05")}
    assert c.economic_limits.max_evidence_cost_usd == Decimal("0.1")


def test_amount_cap(settings):
    assert build("Buy up to 1000 USDC of TOKEN-X if liquidity exceeds $500K", settings).action.max_amount.value == Decimal("1000")
    with pytest.raises(IntentCompilationError) as exc:
        build("Buy up to 1001 USDC of TOKEN-X if liquidity exceeds $500K", settings)
    assert exc.value.code == "AMOUNT_CAP_EXCEEDED"
    assert "1000 USDC" in str(exc.value)
    with pytest.raises(IntentCompilationError):
        build(GRAND_PRIZE, make_settings(max_protected_amount_usdc=Decimal("50")))


def test_non_usdc_amount_rejected():
    with pytest.raises(IntentCompilationError) as exc:
        build("Buy up to 100 EUR of TOKEN-X if liquidity > 500K")
    assert exc.value.code == "UNSUPPORTED_CURRENCY"


def test_missing_parts_rejected_with_helpful_message():
    with pytest.raises(IntentCompilationError) as exc:
        build("liquidity exceeds $500K")
    assert exc.value.code == "INTENT_INCOMPLETE"
    msg = str(exc.value)
    assert "action" in msg and "asset" in msg and "maximum amount" in msg and "Example:" in msg


def test_transfer_without_conditions_rejected():
    with pytest.raises(IntentCompilationError) as exc:
        build("Transfer 25 USDC")
    assert exc.value.code == "NO_CONDITIONS"


def _parsed(**kw):
    base = dict(action=ActionType.BUY, asset="TOKEN-X", max_amount=Decimal("100"), currency="USDC")
    base.update(kw)
    return ParsedIntent(**base)


@pytest.mark.parametrize(
    "condition,problem",
    [
        (ParsedCondition(claim="rsi_14", operator=Operator.LT, threshold=Decimal(30)), "unknown claim"),
        (ParsedCondition(claim=PRICE, operator=Operator.IN, values=["1"]), "not allowed"),
        (ParsedCondition(claim=LIQ, operator=Operator.GT), "finite threshold"),
        (ParsedCondition(claim=LIQ, operator=Operator.GT, threshold=Decimal(1), max_age_seconds=100_000), "max_age_seconds"),
    ],
)
def test_validation_errors_propagate(condition, problem, settings):
    with pytest.raises(InvalidContractError) as exc:
        build_contract(_parsed(conditions=[condition]), intent_id="i", contract_id="c", settings=settings)
    assert any(problem in p for p in exc.value.problems)


def test_invalid_ttl_setting_is_rejected():
    with pytest.raises(InvalidContractError):
        build(GRAND_PRIZE, make_settings(default_authorization_ttl_seconds=0))


def test_thresholds_are_plain_decimals(settings):
    c = build("Buy up to 100 USDC of TOKEN-X if inflows exceed $1M and price is below $0.50", settings)
    assert [str(r.threshold) for r in c.requirements] == ["1000000", "0.5"]
    assert str(c.action.max_amount.value) == "100"


def test_enforce_limits(settings):
    c = build(GRAND_PRIZE, settings)
    enforce_limits(c, settings)
    big = c.model_copy(update={"economic_limits": c.economic_limits.model_copy(update={"max_execution_value_usd": Decimal("5000")})})
    with pytest.raises(IntentCompilationError) as exc:
        enforce_limits(big, settings)
    assert exc.value.code == "AMOUNT_CAP_EXCEEDED"
