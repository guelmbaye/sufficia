"""Intent preservation: every relaxation type is detected, stricter drafts are accepted,
REFERENCE_ONLY never passes silently, and contract-level edits are compared too."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from intent_helpers import GRAND_PRIZE, make_settings
from sufficia.domain.enums import ActionType, ConflictPolicy, DecisionState, Operator
from sufficia.intent import (
    CheckKind,
    ParsedCondition,
    PreservationStatus,
    build_contract,
    check_contract_preservation,
    check_preservation,
    parse_intent,
)
from sufficia.intent.preservation import implies

IN, LIQ, PRICE = "smart_money_inflow_24h", "available_liquidity", "asset_price"
D = Decimal


@pytest.fixture
def ref():
    return parse_intent(GRAND_PRIZE)


def with_conditions(parsed, *conds):
    return parsed.model_copy(update={"conditions": list(conds), "source": "llm"})


def C(claim, op, threshold=None, age=None, **kw):
    return ParsedCondition(claim=claim, operator=Operator(op), threshold=None if threshold is None else D(str(threshold)), max_age_seconds=age, **kw)


def failed(report):
    return {c.check: c for c in report.checks if not c.ok}


def test_identical_draft_is_preserved(ref):
    report = check_preservation(ref, ref.model_copy(update={"source": "llm"}))
    assert report.status == PreservationStatus.PRESERVED
    assert report.acceptable and report.reference_verified
    assert all(c.ok for c in report.checks)
    checks = {c.check for c in report.checks}
    assert {"action", "asset", "currency", "max_amount", f"condition:{IN} > 1000000", f"condition:{LIQ} > 500000"} <= checks


# ---- each relaxation type ---------------------------------------------------------------------------------------


def test_lower_threshold_is_a_relaxation(ref):
    draft = with_conditions(ref, C(IN, ">", 1000000), C(LIQ, ">", 50000))  # the Document 02 §25 example
    report = check_preservation(ref, draft)
    assert report.status == PreservationStatus.RELAXATION_DETECTED
    f = failed(report)[f"condition:{LIQ} > 500000"]
    assert f.expected == f"{LIQ} > 500000" and f.actual == f"{LIQ} > 50000"
    assert "Threshold lowered from 500000 to 50000" in f.detail
    assert not report.acceptable


def test_operator_weakening_at_same_threshold_is_a_relaxation(ref):
    draft = with_conditions(ref, C(IN, ">=", 1000000), C(LIQ, ">", 500000))
    report = check_preservation(ref, draft)
    assert report.status == PreservationStatus.RELAXATION_DETECTED
    assert "Operator weakened from '>' to '>='" in failed(report)[f"condition:{IN} > 1000000"].detail


def test_upper_bound_raised_is_a_relaxation():
    ref = parse_intent("Buy up to 100 USDC of TOKEN-X if price is below $0.50")
    for op, t in (("<", "0.6"), ("<=", "0.5")):
        report = check_preservation(ref, with_conditions(ref, C(PRICE, op, t)))
        assert report.status == PreservationStatus.RELAXATION_DETECTED, (op, t)


def test_flipped_direction_is_a_relaxation(ref):
    report = check_preservation(ref, with_conditions(ref, C(IN, ">", 1000000), C(LIQ, "<", 500000)))
    assert report.status == PreservationStatus.RELAXATION_DETECTED


def test_bigger_amount_is_a_relaxation(ref):
    report = check_preservation(ref, ref.model_copy(update={"max_amount": D("150")}))
    assert report.status == PreservationStatus.RELAXATION_DETECTED
    assert failed(report)["max_amount"].detail == "Maximum amount increased from 100 to 150."


def test_different_asset_is_a_relaxation(ref):
    report = check_preservation(ref, ref.model_copy(update={"asset": "WETH"}))
    assert report.status == PreservationStatus.RELAXATION_DETECTED
    assert failed(report)["asset"].actual == "WETH"


def test_different_action_is_a_relaxation(ref):
    report = check_preservation(ref, ref.model_copy(update={"action": ActionType.SELL}))
    assert report.status == PreservationStatus.RELAXATION_DETECTED


def test_longer_global_max_age_is_a_relaxation():
    ref = parse_intent("Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K, evidence under 15 seconds")
    report = check_preservation(ref, ref.model_copy(update={"max_age_seconds": 60}))
    assert report.status == PreservationStatus.RELAXATION_DETECTED
    assert "max_age_seconds" in failed(report)


def test_longer_max_age_than_the_default_is_a_relaxation(ref):
    report = check_preservation(ref, ref.model_copy(update={"max_age_seconds": 120}), defaults=make_settings())
    assert report.status == PreservationStatus.RELAXATION_DETECTED


def test_longer_per_condition_age_is_a_relaxation():
    ref = parse_intent("Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K (max age 10s)")
    report = check_preservation(ref, with_conditions(ref, C(LIQ, ">", 500000, age=30)))
    assert report.status == PreservationStatus.RELAXATION_DETECTED
    assert failed(report)[f"max_age:{LIQ}"].detail.endswith("increased from 10 s to 30 s.")


def test_dropped_condition_is_a_relaxation(ref):
    report = check_preservation(ref, with_conditions(ref, C(IN, ">", 1000000)))
    assert report.status == PreservationStatus.RELAXATION_DETECTED
    f = failed(report)[f"condition:{LIQ} > 500000"]
    assert f.actual == "missing" and "drops" in f.detail


def test_optional_condition_is_a_relaxation(ref):
    report = check_preservation(ref, with_conditions(ref, C(IN, ">", 1000000), C(LIQ, ">", 500000, required=False)))
    assert report.status == PreservationStatus.RELAXATION_DETECTED
    assert "optional" in failed(report)[f"condition:{LIQ} > 500000"].detail


def test_exists_replaced_by_not_exists_is_a_relaxation():
    ref = parse_intent("Buy up to 100 USDC of TOKEN-X if price exists")
    report = check_preservation(ref, with_conditions(ref, C(PRICE, "NOT_EXISTS")))
    assert report.status != PreservationStatus.PRESERVED


@pytest.mark.parametrize(
    "text,update,check",
    [
        ("Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K, at least 3 sources", {"minimum_required_evidence": 2},
         "minimum_required_evidence"),
        ("Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K, abstain on conflict", {"conflict_policy": ConflictPolicy.ESCALATE},
         "conflict_policy"),
        ("Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K, sources agree within 5%", {"conflict_tolerance": D("0.2")},
         "conflict_tolerance"),
        ("Buy up to 100 USDC of TOKEN-X if liquidity exceeds $500K, evidence budget $0.10", {"evidence_budget_usdc": D("1")},
         "evidence_budget"),
    ],
)
def test_policy_relaxations(text, update, check):
    ref = parse_intent(text)
    report = check_preservation(ref, ref.model_copy(update=update))
    assert report.status == PreservationStatus.RELAXATION_DETECTED
    assert check in failed(report)


# ---- stricter drafts are accepted -------------------------------------------------------------------------------------


def test_stricter_draft_is_preserved_and_extras_reported(ref):
    draft = with_conditions(ref, C(IN, ">", 2000000, age=10), C(LIQ, ">=", 600000), C(PRICE, "<", "0.5")).model_copy(
        update={"max_amount": D("50"), "max_age_seconds": 20, "minimum_required_evidence": 5, "conflict_policy": ConflictPolicy.ABSTAIN}
    )
    report = check_preservation(ref, draft)
    assert report.status == PreservationStatus.PRESERVED
    extras = [c for c in report.checks if c.kind == CheckKind.EXTRA]
    assert [c.check for c in extras] == [f"extra_condition:{PRICE} < 0.5"]
    assert "Additional restrictive condition" in extras[0].detail


def test_strict_operator_at_same_threshold_is_stricter():
    ref = parse_intent("Buy up to 100 USDC of TOKEN-X if liquidity is at least $500K")
    assert check_preservation(ref, with_conditions(ref, C(LIQ, ">", 500000))).status == PreservationStatus.PRESERVED
    assert check_preservation(ref, with_conditions(ref, C(LIQ, ">=", 500001))).status == PreservationStatus.PRESERVED


def test_range_reference_needs_both_bounds():
    ref = parse_intent("Buy up to 100 USDC of TOKEN-X if price is between $0.40 and $0.60")
    ok = with_conditions(ref, C(PRICE, ">=", "0.45"), C(PRICE, "<", "0.55"))
    assert check_preservation(ref, ok).status == PreservationStatus.PRESERVED
    half = with_conditions(ref, C(PRICE, ">=", "0.45"))
    assert check_preservation(ref, half).status == PreservationStatus.RELAXATION_DETECTED


# ---- mismatch / REFERENCE_ONLY --------------------------------------------------------------------------------------


def test_currency_mismatch(ref):
    report = check_preservation(ref, ref.model_copy(update={"currency": "EUR"}))
    assert report.status == PreservationStatus.MISMATCH
    assert not report.acceptable


def test_unknown_claim_in_draft_is_a_mismatch(ref):
    report = check_preservation(ref, with_conditions(ref, *ref.conditions, C("rsi_14", "<", 30)))
    assert report.status == PreservationStatus.MISMATCH
    assert any(c.check.startswith("draft_condition:rsi_14") for c in report.failed)


def test_reference_only_is_flagged_not_silent():
    ref = parse_intent("Buy up to 100 USDC of TOKEN-X if whales are accumulating")
    assert ref.conditions == []
    draft = with_conditions(ref, C(IN, ">", 0))
    report = check_preservation(ref, draft)
    assert report.status == PreservationStatus.REFERENCE_ONLY
    assert report.acceptable and not report.reference_verified
    note = next(c for c in report.checks if c.check == "reference_verification")
    assert note.kind == CheckKind.UNVERIFIED and "could not be verified" in note.detail
    assert "could not be verified" in report.explanation()


def test_reference_only_with_unknown_claim_is_rejected():
    ref = parse_intent("Buy up to 100 USDC of TOKEN-X if whales are accumulating")
    report = check_preservation(ref, with_conditions(ref, C("whale_accumulation", ">", 0)))
    assert report.status == PreservationStatus.MISMATCH
    assert not report.acceptable


def test_reference_only_still_checks_amount_and_asset():
    ref = parse_intent("Buy up to 100 USDC of TOKEN-X if whales are accumulating")
    draft = with_conditions(ref, C(IN, ">", 0)).model_copy(update={"max_amount": D("500")})
    assert check_preservation(ref, draft).status == PreservationStatus.RELAXATION_DETECTED


def test_no_conditions_on_either_side_is_preserved():
    ref = parse_intent("Buy up to 100 USDC of TOKEN-X")
    assert check_preservation(ref, ref.model_copy()).status == PreservationStatus.PRESERVED


def test_incomplete_reference_cannot_verify_draft():
    ref = parse_intent("Buy TOKEN-X if liquidity exceeds $500K")
    draft = ref.model_copy(update={"max_amount": D("100"), "currency": "USDC"})
    report = check_preservation(ref, draft)
    assert report.status == PreservationStatus.MISMATCH
    assert "max_amount" in failed(report)


# ---- implication table ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "draft,reference,expected",
    [
        ((">", "10"), (">", "10"), True), ((">", "11"), (">", "10"), True), ((">", "9"), (">", "10"), False),
        ((">=", "10"), (">", "10"), False), ((">=", "11"), (">", "10"), True), ((">", "10"), (">=", "10"), True),
        ((">=", "10"), (">=", "10"), True), ((">=", "9"), (">=", "10"), False),
        (("<", "10"), ("<", "10"), True), (("<", "9"), ("<", "10"), True), (("<", "11"), ("<", "10"), False),
        (("<=", "10"), ("<", "10"), False), (("<=", "9"), ("<", "10"), True), (("<", "10"), ("<=", "10"), True),
        (("==", "10"), (">=", "10"), True), (("==", "10"), (">", "10"), False), ((">", "10"), ("<", "20"), False),
        ((">", "10"), ("EXISTS", None), True), (("EXISTS", None), (">", "10"), False), (("NOT_EXISTS", None), ("EXISTS", None), False),
    ],
)
def test_implies(draft, reference, expected):
    assert implies(C(PRICE, *draft), C(PRICE, *reference)) is expected


# ---- contract level (user-edited contracts) --------------------------------------------------------------------------


@pytest.fixture
def ref_contract(settings):
    return build_contract(parse_intent(GRAND_PRIZE), intent_id="i", contract_id="c", settings=settings)


def edit(contract, **updates):
    return contract.model_copy(update=updates, deep=True)


def edit_req(contract, req_id, **updates):
    reqs = [r.model_copy(update=updates) if r.id == req_id else r for r in contract.requirements]
    return contract.model_copy(update={"requirements": reqs})


def test_contract_identical_is_preserved(ref_contract):
    assert check_contract_preservation(ref_contract, ref_contract.model_copy(deep=True)).status == PreservationStatus.PRESERVED


@pytest.mark.parametrize(
    "mutate,check",
    [
        (lambda c: edit_req(c, "req_liquidity", threshold=D("50000")), f"condition:{LIQ} > 500000"),
        (lambda c: edit_req(c, "req_inflow", operator=Operator.GTE), f"condition:{IN} > 1000000"),
        (lambda c: edit_req(c, "req_liquidity", max_age_seconds=300), f"max_age:{LIQ}"),
        (lambda c: edit_req(c, "req_liquidity", conflict_tolerance=D("0.5")), f"conflict_tolerance:{LIQ}"),
        (lambda c: edit_req(c, "req_liquidity", required=False), f"condition:{LIQ} > 500000"),
        (lambda c: c.model_copy(update={"requirements": [r for r in c.requirements if r.id != "req_price"]}), f"condition:{PRICE} EXISTS"),
        (lambda c: edit(c, authorization=c.authorization.model_copy(update={"ttl_seconds": 120})), "authorization_ttl"),
        (lambda c: edit(c, policy=c.policy.model_copy(update={"minimum_required_evidence": 1})), "minimum_required_evidence"),
        (lambda c: edit(c, policy=c.policy.model_copy(update={"missing_required": DecisionState.ESCALATE})), "policy.missing_required"),
        (lambda c: edit(c, policy=c.policy.model_copy(update={"clock_skew_seconds": 10})), "clock_skew_seconds"),
        (lambda c: edit(c, economic_limits=c.economic_limits.model_copy(update={"max_evidence_cost_usd": D("5")})), "evidence_budget"),
        (lambda c: edit(c, action=c.action.model_copy(update={"max_amount": c.action.max_amount.model_copy(update={"value": D("500")})})),
         "max_amount"),
    ],
)
def test_contract_relaxations_detected(ref_contract, mutate, check):
    report = check_contract_preservation(ref_contract, mutate(ref_contract))
    assert report.status == PreservationStatus.RELAXATION_DETECTED
    assert check in failed(report)


def test_contract_stricter_edit_is_preserved(ref_contract):
    stricter = edit_req(ref_contract, "req_liquidity", threshold=D("750000"), max_age_seconds=10, minimum_items=2)
    stricter = edit(stricter, authorization=stricter.authorization.model_copy(update={"ttl_seconds": 15}))
    assert check_contract_preservation(ref_contract, stricter).status == PreservationStatus.PRESERVED


def test_report_is_json_serializable_with_documented_shape(ref):
    report = check_preservation(ref, with_conditions(ref, C(IN, ">", 1000000), C(LIQ, ">", 50000)))
    data = json.loads(json.dumps(report.to_dict()))
    assert data["status"] == "RELAXATION_DETECTED"
    assert set(data["checks"][0]) >= {"check", "expected", "actual", "ok", "detail"}


def test_preservation_is_deterministic(ref):
    draft = with_conditions(ref, C(IN, ">", 1000000), C(LIQ, ">", 50000))
    assert check_preservation(ref, draft).to_dict() == check_preservation(ref, draft).to_dict()
