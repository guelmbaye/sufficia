"""Decision Engine semantics: operators, freshness, conflicts, dedupe, independence, validation,
budget, coverage, policy, purity and explanation fidelity (Documents 02/03/06)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import ROUND_UP, Decimal

import pytest

import engine_builders as b
from sufficia.benchmark.runner import check_explanations
from sufficia.decision_engine import (
    ENGINE_VERSION,
    InvalidContractError,
    UnsupportedEngineVersion,
    evaluate,
    validate_contract,
)
from sufficia.decision_engine import engine as engine_module
from sufficia.decision_engine import explain, rules
from sufficia.domain.enums import DecisionState, EvidenceStatus, Operator, RequirementStatus
from sufficia.domain.models import Decision, EvidenceContract, Requirement

T0 = b.T0
A, D, AB, R, E = (DecisionState.AUTHORIZE, DecisionState.DENY, DecisionState.ABSTAIN, DecisionState.REFRESH,
                  DecisionState.ESCALATE)


def single(claim: str, operator: str, *, unit: str = "USD", **req) -> EvidenceContract:
    """One-requirement contract (minimum evidence 1)."""
    r = {"id": "r1", "claim": claim, "operator": operator, "unit": unit, **req}
    return b.make_contract(requirements=[r], policy={"minimum_required_evidence": 1})


def one(value, *, claim="available_liquidity", unit="USD", **kw):
    return b.make_item("r1", value, claim=claim, unit=unit, evidence_id=kw.pop("evidence_id", "e1"), **kw)


# =============================================================================================
# Baseline states
# =============================================================================================


def test_all_valid_authorizes():
    d = b.decide()
    assert d.state == A
    assert b.codes(d) == ["ALL_REQUIREMENTS_SATISFIED"]
    assert d.authority_window is not None
    assert [r.status for r in d.requirement_results] == [RequirementStatus.PASS] * 3
    assert d.coverage == Decimal("1.0000") and d.freshness_pass and d.conflicts == 0
    assert d.engine_version == ENGINE_VERSION and d.evaluated_at == T0
    assert d.decision_id == "dec_" + d.fingerprint[2:18]


# =============================================================================================
# Operators
# =============================================================================================

NUMERIC_CASES = [
    # operator, threshold, value, satisfied
    (">", 500000, 500001, True), (">", 500000, 500000, False), (">", 500000, 499999, False),
    (">=", 500000, 500000, True), (">=", 500000, Decimal("499999.99"), False), (">=", 500000, 500001, True),
    ("<", 500000, 499999, True), ("<", 500000, 500000, False), ("<", 500000, 500001, False),
    ("<=", 500000, 500000, True), ("<=", 500000, Decimal("500000.01"), False), ("<=", 500000, 1, True),
    ("==", 500000, Decimal("500000.00"), True), ("==", 500000, Decimal("500000.01"), False),
    ("!=", 500000, 500001, True), ("!=", 500000, Decimal("500000.000"), False),
]


@pytest.mark.parametrize("op, threshold, value, ok", NUMERIC_CASES)
def test_numeric_operators(op, threshold, value, ok):
    c = single("available_liquidity", op, threshold=Decimal(threshold))
    d = evaluate(c, b.make_set([one(value)]), T0)
    assert d.state == (A if ok else D)
    if not ok:
        assert b.codes(d) == ["THRESHOLD_NOT_MET"]
        assert d.reasons[0].detail["observed_values"] == [rules_fmt(value)]


def rules_fmt(v):
    return engine_module._fmt(Decimal(v))


@pytest.mark.parametrize("op, threshold, value, ok", NUMERIC_CASES)
def test_rules_satisfies_matches_engine(op, threshold, value, ok):
    req = Requirement(id="r1", claim="available_liquidity", operator=op, threshold=Decimal(threshold))
    assert rules.satisfies(Decimal(value), req) is ok


def test_exists_accepts_any_valid_value_including_zero():
    c = single("asset_price", "EXISTS")
    for v in (Decimal("0.428"), 0, Decimal("1e12")):
        assert evaluate(c, b.make_set([one(v, claim="asset_price")]), T0).state == A
    assert evaluate(c, b.make_set([]), T0).state == AB


def test_exists_does_not_accept_invalid_values():
    c = single("asset_price", "EXISTS")
    d = evaluate(c, b.make_set([one("0.428", claim="asset_price")]), T0)
    assert d.state == AB and d.reasons[0].causes[0].value == "INVALID_EVIDENCE_TYPE"


def _bool_item(value, age=1, evidence_id="s1"):
    return b.make_item("r1", value, claim="sanctions_flag", unit="BOOL", age=age, evidence_id=evidence_id)


def test_not_exists_passes_without_evidence_and_fails_with_present_observation():
    c = single("sanctions_flag", "NOT_EXISTS", unit="BOOL")
    c = c.model_copy(update={"policy": c.policy.model_copy(update={"minimum_required_evidence": 0})})
    assert evaluate(c, b.make_set([]), T0).state == A
    d = evaluate(c, b.make_set([_bool_item(True)]), T0)
    assert d.state == D and b.codes(d) == ["PROHIBITED_EVIDENCE_PRESENT"]
    assert "does not exist" in d.reasons[0].message
    assert evaluate(c, b.make_set([_bool_item(True, age=100)]), T0).state == R
    failed = b.make_set([], failures=[{"requirement_id": "r1", "provider": "p", "code": "SOURCE_UNAVAILABLE"}])
    assert evaluate(c, failed, T0).state == AB  # an unavailable check is not proof of absence


def test_bool_equality_operator():
    c = single("asset_listed", "==", unit="BOOL", expected=True)
    assert evaluate(c, b.make_set([b.make_item("r1", True, claim="asset_listed", unit="BOOL", evidence_id="e")]), T0).state == A
    assert evaluate(c, b.make_set([b.make_item("r1", False, claim="asset_listed", unit="BOOL", evidence_id="e")]), T0).state == D
    # 1 is not True for a BOOL claim: type error, never coerced
    d = evaluate(c, b.make_set([b.make_item("r1", 1, claim="asset_listed", unit="BOOL", evidence_id="e")]), T0)
    assert d.state == AB and d.reasons[0].causes[0].value == "INVALID_EVIDENCE_TYPE"


def test_in_operator_and_identifier_neq():
    c = single("venue_id", "IN", unit="ID", values=["uniswap-v3", "kuru"])
    item = lambda v: b.make_item("r1", v, claim="venue_id", unit="ID", evidence_id="v")  # noqa: E731
    assert evaluate(c, b.make_set([item("kuru")]), T0).state == A
    assert evaluate(c, b.make_set([item("  kuru ")]), T0).state == A  # identifiers are trimmed
    assert evaluate(c, b.make_set([item("sushi")]), T0).state == D
    assert evaluate(c, b.make_set([item("")]), T0).state == AB
    neq = single("venue_id", "!=", unit="ID", expected="blocked-venue")
    assert evaluate(neq, b.make_set([item("kuru")]), T0).state == A
    assert evaluate(neq, b.make_set([item("blocked-venue")]), T0).state == D


def test_rules_satisfies_non_numeric_branches():
    assert rules.satisfies("x", Requirement(id="r", claim="venue_id", operator="EXISTS"))
    assert rules.satisfies("a", Requirement(id="r", claim="venue_id", operator="IN", values=["a"]))
    assert not rules.satisfies("b", Requirement(id="r", claim="venue_id", operator="IN", values=["a"]))
    assert rules.satisfies(True, Requirement(id="r", claim="asset_listed", operator="==", expected=True))
    assert rules.satisfies("x", Requirement(id="r", claim="venue_id", operator="!=", expected="y"))


# =============================================================================================
# Freshness
# =============================================================================================


@pytest.mark.parametrize(
    "age, fresh, state",
    [
        (Decimal("0"), True, "AUTHORIZE"),
        (Decimal("29"), True, "AUTHORIZE"),  # 1 s window: smallest committable authority
        (Decimal("29.999"), True, "WINDOW"),  # fresh, but < 1 whole second of authority remains
        (Decimal("30"), True, "WINDOW"),  # boundary is fresh; zero window
        (Decimal("30.000001"), False, "STALE"),
        (Decimal("30.001"), False, "STALE"),
        (Decimal("31"), False, "STALE"),
        (Decimal("92"), False, "STALE"),
    ],
)
def test_freshness_boundaries(age, fresh, state):
    d = b.decide(b.good_items(req_liquidity={"age": age}))
    a = b.assessment(d, "ev_req_liquidity")
    assert a.status == (EvidenceStatus.VALID if fresh else EvidenceStatus.STALE)
    assert a.age_seconds == age.quantize(Decimal("0.001"), rounding=ROUND_UP)
    if state == "AUTHORIZE":
        assert d.state == A and d.authority_window.expires_at > d.evaluated_at
    elif state == "WINDOW":
        assert d.state == R and b.codes(d) == ["AUTHORITY_WINDOW_EXHAUSTED"]
        assert d.authority_window is None and d.reasons[0].requirement_id == "req_liquidity"
        assert d.freshness_pass is False
    else:
        assert d.state == R
        assert b.codes(d) == ["EVIDENCE_STALE"]
        assert d.reasons[0].requirement_id == "req_liquidity"
        assert d.freshness_pass is False


def test_rules_freshness_helpers():
    assert rules.is_fresh(Decimal(30), 30) and not rules.is_fresh(Decimal("30.000001"), 30)
    assert rules.age_seconds(T0 + timedelta(seconds=1), T0) == 0  # future within skew counts as age 0
    assert rules.exact_seconds(timedelta(days=1, microseconds=1)) == Decimal("86400.000001")
    assert rules.evidence_expiry(T0, 30) == T0 + timedelta(seconds=30)


def test_stale_value_is_not_evaluated_as_current_truth():
    d = b.decide(b.good_items(req_liquidity={"value": Decimal(92440), "age": 92}))
    assert d.state == R and b.codes(d) == ["EVIDENCE_STALE"]
    rr = [r for r in d.requirement_results if r.requirement_id == "req_liquidity"][0]
    assert rr.checks.threshold is None and rr.status == RequirementStatus.UNKNOWN


def test_freshest_stale_observation_is_reported():
    items = b.good_items() + [b.make_item("req_liquidity", evidence_id="ev_liq_old", age=300, provider="p2")]
    items[1] = b.make_item("req_liquidity", age=45)
    d = b.decide(items)
    assert d.state == R
    assert d.reasons[0].detail["evidence_id"] == "ev_req_liquidity"
    assert d.reasons[0].detail["age_seconds"] == "45"


def test_one_fresh_item_satisfies_minimum_one_even_with_stale_sibling():
    items = b.good_items() + [b.make_item("req_liquidity", evidence_id="ev_liq_old", age=300, provider="p2")]
    d = b.decide(items)
    assert d.state == A
    assert b.assessment(d, "ev_liq_old").status == EvidenceStatus.STALE


def test_future_timestamp_within_and_beyond_clock_skew():
    within = b.decide(b.good_items(req_liquidity={"age": -2}))
    assert within.state == A
    beyond = b.decide(b.good_items(req_liquidity={"age": Decimal("-2.000001")}))
    assert beyond.state == AB
    assert beyond.reasons[0].causes[0].value == "FUTURE_TIMESTAMP"
    assert b.assessment(beyond, "ev_req_liquidity").reason_codes[0].value == "FUTURE_TIMESTAMP"


def test_clock_skew_policy_is_respected():
    c = b.make_contract(policy={"minimum_required_evidence": 3, "clock_skew_seconds": 0})
    d = b.decide(b.good_items(req_liquidity={"age": Decimal("-0.001")}), c)
    assert d.state == AB and d.reasons[0].causes[0].value == "FUTURE_TIMESTAMP"


def test_naive_timestamp_is_invalid():
    naive = (T0 - timedelta(seconds=3)).replace(tzinfo=None)
    d = b.decide(b.good_items(req_liquidity={"observed_at": naive}))
    assert d.state == AB and d.reasons[0].causes[0].value == "INVALID_TIMESTAMP"


@pytest.mark.parametrize("observed", [None, "yesterday", "2026-10-05T18:42:10+25:00", "2026-13-45T00:00:00Z"])
def test_missing_or_unparseable_timestamp_is_invalid(observed):
    d = b.decide(b.good_items(req_liquidity={"observed_at": observed}))
    assert d.state == AB
    assert d.reasons[0].causes[0].value == "INVALID_TIMESTAMP"


def test_non_utc_offsets_are_compared_exactly():
    plus2 = timezone(timedelta(hours=2))
    observed = (T0 - timedelta(seconds=10)).astimezone(plus2)
    d = b.decide(b.good_items(req_liquidity={"observed_at": observed}))
    assert d.state == A and b.assessment(d, "ev_req_liquidity").age_seconds == Decimal("10.000")


def test_evaluation_time_must_be_timezone_aware():
    with pytest.raises(ValueError):
        b.decide(t=T0.replace(tzinfo=None))


# =============================================================================================
# Conflicts
# =============================================================================================


def _two_liquidity(v1, v2, **req_changes):
    c = b.make_contract()
    if req_changes:
        c = b.with_requirement(c, "req_liquidity", **req_changes)
    items = b.good_items(req_liquidity={"value": Decimal(v1), "evidence_id": "liq_a", "provider": "a"})
    items.append(b.make_item("req_liquidity", Decimal(v2), evidence_id="liq_b", provider="b"))
    return b.decide(items, c)


@pytest.mark.parametrize(
    "v1, v2, conflict",
    [(1_000_000, 901_000, False), (1_000_000, 900_000, False), (1_000_000, Decimal("899999.99"), True),
     (1_000_000, 899_000, True), (718_000, 695_000, False), (718_000, 91_000, True)],
)
def test_conflict_boundary_is_strictly_greater_than_tolerance(v1, v2, conflict):
    d = _two_liquidity(v1, v2)
    assert d.state == (E if conflict else A)
    if conflict:
        assert b.codes(d) == ["CRITICAL_SOURCE_CONFLICT"] and d.conflicts == 1
        assert {b.assessment(d, "liq_a").status, b.assessment(d, "liq_b").status} == {EvidenceStatus.CONFLICTED}


def test_conflict_tolerance_zero_and_one():
    assert _two_liquidity(718_000, 718_000, conflict_tolerance=Decimal(0)).state == A
    assert _two_liquidity(718_000, 718_001, conflict_tolerance=Decimal(0)).state == E
    assert _two_liquidity(718_000, 600_000, conflict_tolerance=Decimal(1)).state == A


def test_relative_difference_helper():
    assert rules.relative_difference(Decimal(0), Decimal(0)) == 0
    assert rules.relative_difference(Decimal(1000), Decimal(900)) == Decimal("0.1")
    assert rules.relative_difference(Decimal(-100), Decimal(100)) == Decimal(2)


def test_non_numeric_observations_conflict_when_different():
    c = single("venue_id", "EXISTS", unit="ID")
    items = [b.make_item("r1", "kuru", claim="venue_id", unit="ID", evidence_id="v1", provider="a"),
             b.make_item("r1", "sushi", claim="venue_id", unit="ID", evidence_id="v2", provider="b")]
    d = evaluate(c, b.make_set(items), T0)
    assert d.state == E
    assert d.reasons[0].detail["relative_difference"] == "0"


def test_conflict_precedes_threshold_failure():
    d = _two_liquidity(718_000, 91_000)
    items = [b.make_item("req_inflow", Decimal(640_000))] + [i for i in b.good_items()[1:]]
    items.append(b.make_item("req_liquidity", Decimal(91_000), evidence_id="liq_b", provider="b"))
    d = b.decide(items)
    assert d.state == E and b.codes(d) == ["CRITICAL_SOURCE_CONFLICT"]


def test_conflict_policy_abstain():
    d = _two_liquidity(718_000, 91_000, conflict_policy="ABSTAIN")
    assert d.state == AB and b.codes(d) == ["CRITICAL_SOURCE_CONFLICT"]


def test_every_fresh_observation_must_pass_no_averaging():
    d = _two_liquidity(510_000, 495_000)  # 2.9% apart: no conflict, one fails
    assert d.state == D
    assert d.reasons[0].detail["observed_values"] == ["495000"]


def test_stale_observations_do_not_create_conflicts():
    items = b.good_items() + [b.make_item("req_liquidity", Decimal(91_000), evidence_id="liq_old", provider="b", age=100)]
    assert b.decide(items).state == A


def test_invalid_observations_do_not_create_conflicts():
    items = b.good_items() + [b.make_item("req_liquidity", Decimal(91_000), evidence_id="liq_bad", provider="b", unit="JPY")]
    d = b.decide(items)
    assert d.state == A
    assert b.assessment(d, "liq_bad").status == EvidenceStatus.INVALID


# =============================================================================================
# Deduplication and independence
# =============================================================================================


def test_duplicate_evidence_id_cannot_inflate_coverage_or_count():
    c = b.make_contract(policy={"minimum_required_evidence": 4})
    items = b.good_items() + [b.good_items()[1]]  # same item twice
    d = b.decide(items, c)
    assert d.state == AB and b.codes(d) == ["INSUFFICIENT_EVIDENCE_COUNT"]
    assert d.evidence_count == 3
    statuses = sorted(a.status.value for a in d.evidence_assessments if a.evidence_id == "ev_req_liquidity")
    assert statuses == ["DUPLICATE", "VALID"]


def test_same_observation_under_new_id_is_duplicate():
    c = b.with_requirement(b.make_contract(policy={"minimum_required_evidence": 4}), "req_liquidity", minimum_items=2)
    items = b.good_items() + [b.make_item("req_liquidity", evidence_id="ev_liq_copy")]
    d = b.decide(items, c)
    assert d.state == AB and b.codes(d) == ["MISSING_REQUIRED_EVIDENCE"]
    # items are processed in evidence_id order: the first copy is kept, the second is DUPLICATE
    assert b.assessment(d, "ev_liq_copy").status == EvidenceStatus.VALID
    assert b.assessment(d, "ev_req_liquidity").status == EvidenceStatus.DUPLICATE
    assert d.reasons[0].detail["valid_items"] == 1


def test_duplicates_do_not_count_toward_minimum_required_evidence():
    c = b.make_contract(policy={"minimum_required_evidence": 4})
    items = b.good_items() + [b.make_item("req_price", evidence_id="ev_px_copy")]
    d = b.decide(items, c)
    assert d.state == AB and b.codes(d) == ["INSUFFICIENT_EVIDENCE_COUNT"]
    assert d.reasons[0].detail == {"valid_items": 3, "minimum": 4}


def test_distinct_observations_satisfy_minimum_items():
    c = b.with_requirement(b.make_contract(), "req_liquidity", minimum_items=2)
    items = b.good_items() + [b.make_item("req_liquidity", Decimal(700_000), evidence_id="liq2", provider="pool")]
    assert b.decide(items, c).state == A


def test_three_items_sharing_an_upstream_count_as_one_source():
    items = [b.make_item(rid, provider=f"api-{i}", source_family=None, upstream_source="chainlink:TOKENX/USD")
             for i, rid in enumerate(b.REQUIREMENTS)]
    d = b.decide(items)
    assert d.state == A
    assert d.evidence_count == 3
    assert d.independent_source_count == 1 and d.independence_known is True


def test_unknown_family_means_independence_unknown():
    items = b.good_items(req_price={"source_family": None})
    d = b.decide(items)
    assert d.independence_known is False and d.independent_source_count is None
    assert d.state == A  # independence is reported, not a requirement of this contract


def test_distinct_families_are_counted():
    d = b.decide()
    assert d.independence_known and d.independent_source_count == 3


def test_no_evidence_means_independence_unknown():
    d = b.decide([])
    assert d.independence_known is False and d.independent_source_count is None


# =============================================================================================
# Item validation
# =============================================================================================


def test_wrong_contract_item_is_rejected():
    d = b.decide(b.good_items(req_liquidity={"contract_id": "ec_other"}))
    assert d.state == AB and [c.value for c in d.reasons[0].causes] == ["CONTRACT_MISMATCH"]


def test_wrong_requirement_item_is_rejected_and_claim_unsupported():
    items = b.good_items()
    items[1] = b.make_item("req_unknown", Decimal(718000), claim="available_liquidity", evidence_id="ev_liq")
    d = b.decide(items)
    assert d.state == AB and d.reasons[0].requirement_id == "req_liquidity"
    assert [c.value for c in b.assessment(d, "ev_liq").reason_codes] == ["UNKNOWN_REQUIREMENT"]


def test_claim_mismatch_is_rejected():
    d = b.decide(b.good_items(req_liquidity={"claim": "asset_price"}))
    assert d.state == AB and [c.value for c in d.reasons[0].causes] == ["CLAIM_MISMATCH"]


def test_evidence_set_for_another_contract_raises():
    with pytest.raises(ValueError):
        evaluate(b.make_contract(), b.make_set(b.good_items(), contract_id="ec_other"), T0)


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"unit": "JPY"}, "INVALID_UNIT"),
        ({"unit": None}, "INVALID_UNIT"),
        ({"unit": "usd"}, "INVALID_UNIT"),
        ({"request_hash": None}, "PROVENANCE_MISSING"),
        ({"response_hash": None}, "PROVENANCE_MISSING"),
        ({"response_hash": "0x" + "b2" * 31}, "PROVENANCE_MISSING"),
        ({"response_hash": "b2" * 32}, "PROVENANCE_MISSING"),
        ({"request_hash": "0x" + "zz" * 32}, "PROVENANCE_MISSING"),
        ({"provider": ""}, "PROVENANCE_MISSING"),
        ({"value": "718000"}, "INVALID_EVIDENCE_TYPE"),
        ({"value": True}, "INVALID_EVIDENCE_TYPE"),
        ({"value": [718000]}, "INVALID_EVIDENCE_TYPE"),
        ({"value": Decimal("-1")}, "INVALID_VALUE"),
        ({"value": Decimal("NaN")}, "INVALID_VALUE"),
        ({"value": Decimal("1e19")}, "INVALID_VALUE"),
    ],
)
def test_invalid_mandatory_liquidity_abstains_with_cause(overrides, code):
    d = b.decide(b.good_items(req_liquidity=overrides))
    assert d.state == AB and b.codes(d) == ["MISSING_REQUIRED_EVIDENCE"]
    assert [c.value for c in d.reasons[0].causes] == [code]
    assert b.assessment(d, "ev_req_liquidity").status == EvidenceStatus.INVALID


def test_onchain_and_oracle_provenance_accept_chain_reference():
    ref = {"chain_id": 10143, "block_number": 1}
    for st in ("ONCHAIN", "ORACLE"):
        d = b.decide(b.good_items(req_price={"source_type": st, "request_hash": None, "chain_ref": ref}))
        assert d.state == A
        d = b.decide(b.good_items(req_price={"source_type": st, "request_hash": None}))
        assert d.state == AB and d.reasons[0].causes[0].value == "PROVENANCE_MISSING"


def test_derived_evidence_needs_lineage():
    ok = b.decide(b.good_items(req_inflow={"source_type": "DERIVED", "request_hash": None,
                                           "derivation": {"method": "sum", "version": "1", "inputs": ["ev_a", "ev_b"]}}))
    assert ok.state == A
    no_inputs = b.decide(b.good_items(req_inflow={"source_type": "DERIVED",
                                                  "derivation": {"method": "sum", "version": "1", "inputs": []}}))
    assert no_inputs.state == AB and no_inputs.reasons[0].causes[0].value == "PROVENANCE_MISSING"


def test_permitted_sources_registry():
    contract = b.make_contract()
    evs = b.make_set(b.good_items())
    permitted = {"smartmoney-api": {"smart_money_inflow_24h"}, "dex-liquidity-api": {"available_liquidity"},
                 "price-api": {"asset_price"}}
    assert evaluate(contract, evs, T0, permitted_sources=permitted).state == A
    del permitted["dex-liquidity-api"]
    d = evaluate(contract, evs, T0, permitted_sources=permitted)
    assert d.state == AB and d.reasons[0].causes[0].value == "SOURCE_NOT_PERMITTED"
    wrong_claim = {**permitted, "dex-liquidity-api": {"asset_price"}}
    assert evaluate(contract, evs, T0, permitted_sources=wrong_claim).state == AB


def test_multiple_invalidity_causes_are_all_reported_sorted():
    d = b.decide(b.good_items(req_liquidity={"value": "lots", "unit": "BTC", "response_hash": None}))
    assert [c.value for c in d.reasons[0].causes] == ["INVALID_EVIDENCE_TYPE", "INVALID_UNIT", "PROVENANCE_MISSING"]


def test_negative_inflow_is_valid_and_fails_threshold():
    d = b.decide(b.good_items(req_inflow={"value": Decimal(-250_000)}))
    assert d.state == D and d.reasons[0].detail["observed_values"] == ["-250000"]


# =============================================================================================
# Missing, budget, minimum evidence
# =============================================================================================


def test_missing_mandatory_evidence_abstains():
    d = b.decide(b.good_items()[:2])
    assert d.state == AB and b.codes(d) == ["MISSING_REQUIRED_EVIDENCE"]
    assert d.reasons[0].requirement_id == "req_price" and d.reasons[0].causes == []
    assert d.coverage == Decimal("0.6667")


def test_acquisition_failure_cause_is_reported():
    failures = [{"requirement_id": "req_liquidity", "provider": "dex", "code": "SOURCE_UNAVAILABLE", "detail": "503"}]
    d = b.decide([b.good_items()[0], b.good_items()[2]], failures=failures)
    assert d.state == AB and [c.value for c in d.reasons[0].causes] == ["SOURCE_UNAVAILABLE"]
    assert "the source was unavailable" in d.reasons[0].message


def test_acquisition_failure_does_not_block_when_valid_evidence_exists():
    failures = [{"requirement_id": "req_liquidity", "provider": "backup", "code": "RATE_LIMITED"}]
    assert b.decide(failures=failures).state == A


def test_budget_exhaustion_via_item_costs():
    items = b.good_items(req_inflow={"cost": "0.15"}, req_liquidity={"cost": "0.08"}, req_price={"cost": "0.03"})
    d = b.decide(items)
    assert d.state == AB and b.codes(d) == ["EVIDENCE_BUDGET_EXHAUSTED"]
    assert d.reasons[0].requirement_id is None
    assert d.reasons[0].detail == {"budget": "0.25", "spent": "0.26"}
    assert d.evidence_cost_usd == Decimal("0.26")


def test_budget_exactly_spent_is_allowed():
    items = b.good_items(req_inflow={"cost": "0.15"}, req_liquidity={"cost": "0.07"}, req_price={"cost": "0.03"})
    assert b.decide(items).state == A


def test_budget_exhaustion_via_acquisition_failure_costs():
    failures = [{"requirement_id": "req_liquidity", "provider": "backup", "code": "PAYMENT_FAILED", "cost_spent": Decimal("0.30")}]
    d = b.decide(failures=failures)
    assert d.state == AB and b.codes(d) == ["EVIDENCE_BUDGET_EXHAUSTED"]
    assert d.evidence_cost_usd == Decimal("0.30")


def test_budget_exhaustion_via_budget_exceeded_acquisition_failure():
    failures = [{"requirement_id": "req_liquidity", "provider": "dex", "code": "BUDGET_EXCEEDED"}]
    d = b.decide([b.good_items()[0], b.good_items()[2]], failures=failures)
    assert d.state == AB and b.codes(d) == ["EVIDENCE_BUDGET_EXHAUSTED"]
    assert d.reasons[0].requirement_id == "req_liquidity"
    assert [c.value for c in d.reasons[0].causes] == ["BUDGET_EXCEEDED"]
    rr = [r for r in d.requirement_results if r.requirement_id == "req_liquidity"][0]
    assert rr.reason_code.value == "EVIDENCE_BUDGET_EXHAUSTED"


def test_duplicate_item_cost_counted_once():
    items = b.good_items(req_liquidity={"cost": "0.2"})
    d = b.decide(items + [items[1]])
    assert d.evidence_cost_usd == Decimal("0.2") and d.state == A


def test_budget_and_missing_reasons_are_both_reported():
    items = b.good_items(req_inflow={"cost": "0.30"})[:2]
    d = b.decide(items)
    assert d.state == AB
    assert b.codes(d) == ["EVIDENCE_BUDGET_EXHAUSTED", "MISSING_REQUIRED_EVIDENCE"]


def test_minimum_required_evidence():
    assert b.decide(contract=b.make_contract(policy={"minimum_required_evidence": 3})).state == A
    d = b.decide(contract=b.make_contract(policy={"minimum_required_evidence": 4}))
    assert d.state == AB and b.codes(d) == ["INSUFFICIENT_EVIDENCE_COUNT"]
    assert d.reasons[0].message == ("Only 3 valid evidence item(s) are available; "
                                    "the Evidence Contract requires at least 4.")


def test_minimum_items_with_one_observation_is_missing():
    c = b.with_requirement(b.make_contract(), "req_liquidity", minimum_items=2)
    d = b.decide(contract=c)
    assert d.state == AB and d.reasons[0].requirement_id == "req_liquidity"
    assert d.reasons[0].message.startswith("Only 1 valid available liquidity observation(s); at least 2 are required")


def test_minimum_items_with_fresh_and_stale_is_refresh():
    c = b.with_requirement(b.make_contract(), "req_liquidity", minimum_items=2)
    items = b.good_items() + [b.make_item("req_liquidity", Decimal(700_000), evidence_id="liq2", provider="pool", age=45)]
    assert b.decide(items, c).state == R


# =============================================================================================
# Precedence
# =============================================================================================


def test_precedence_missing_over_stale_conflict_and_fail():
    items = [b.make_item("req_inflow", Decimal(640_000)),  # fail
             b.make_item("req_liquidity", evidence_id="a", provider="a"),
             b.make_item("req_liquidity", Decimal(91_000), evidence_id="b", provider="b")]  # conflict; price missing
    assert b.decide(items).state == AB


def test_precedence_stale_over_conflict_and_fail():
    items = [b.make_item("req_inflow", Decimal(640_000)),
             b.make_item("req_liquidity", evidence_id="a", provider="a"),
             b.make_item("req_liquidity", Decimal(91_000), evidence_id="b", provider="b"),
             b.make_item("req_price", age=40)]
    d = b.decide(items)
    assert d.state == R and b.codes(d) == ["EVIDENCE_STALE"]


def test_precedence_conflict_over_fail():
    items = [b.make_item("req_inflow", Decimal(640_000)),
             b.make_item("req_liquidity", evidence_id="a", provider="a"),
             b.make_item("req_liquidity", Decimal(91_000), evidence_id="b", provider="b"),
             b.make_item("req_price")]
    assert b.decide(items).state == E


def test_multiple_failures_all_reported_in_requirement_order():
    d = b.decide(b.good_items(req_inflow={"value": Decimal(1)}, req_liquidity={"value": Decimal(2)}))
    assert d.state == D and [r.requirement_id for r in d.reasons] == ["req_inflow", "req_liquidity"]
    assert b.codes(d) == ["THRESHOLD_NOT_MET"]  # deduplicated code list


# =============================================================================================
# Optional requirements
# =============================================================================================


def _optional_liquidity(**kw):
    c = b.with_requirement(b.make_contract(policy={"minimum_required_evidence": 2}), "req_liquidity", required=False)
    return c


@pytest.mark.parametrize(
    "liquidity",
    [None, {"value": Decimal(1)}, {"age": 300}, {"unit": "BTC"}, "conflict"],
)
def test_optional_requirements_never_block(liquidity):
    c = _optional_liquidity()
    items = [b.make_item("req_inflow"), b.make_item("req_price")]
    if liquidity == "conflict":
        items += [b.make_item("req_liquidity", evidence_id="a", provider="a"),
                  b.make_item("req_liquidity", Decimal(91_000), evidence_id="b", provider="b")]
    elif liquidity is not None:
        items.append(b.make_item("req_liquidity", **liquidity))
    d = b.decide(items, c)
    assert d.state == A
    assert d.coverage == Decimal("1.0000")
    assert d.authority_window.bounded_by != "EVIDENCE_EXPIRY:ev_req_liquidity"


def test_optional_items_do_not_count_toward_minimum_required_evidence():
    c = b.with_requirement(b.make_contract(policy={"minimum_required_evidence": 3}), "req_liquidity", required=False)
    d = b.decide(contract=c)
    assert d.state == AB and b.codes(d) == ["INSUFFICIENT_EVIDENCE_COUNT"]


def test_contract_with_only_optional_requirements():
    c = b.make_contract(policy={"minimum_required_evidence": 0},
                        requirements=[{"id": "r1", "claim": "asset_price", "operator": "EXISTS", "unit": "USD", "required": False}])
    d = evaluate(c, b.make_set([]), T0)
    assert d.state == A and d.coverage == Decimal("1.0000")
    assert d.authority_window.bounded_by == "CONTRACT_TTL"


# =============================================================================================
# Authority window
# =============================================================================================


def test_authority_window_bounded_by_oldest_mandatory_evidence():
    d = b.decide(b.good_items(req_price={"age": 25}))
    w = d.authority_window
    assert w.expires_at == T0 + timedelta(seconds=5)
    assert w.bounded_by == "EVIDENCE_EXPIRY:ev_req_price" and w.ttl_seconds == Decimal("5.000")
    assert w.issued_at == T0


def test_authority_window_bounded_by_contract_ttl():
    d = b.decide(b.good_items(req_inflow={"age": 0}, req_liquidity={"age": 0}, req_price={"age": 0}),
                 b.make_contract(authorization={"ttl_seconds": 10}))
    assert d.authority_window.bounded_by == "CONTRACT_TTL"
    assert d.authority_window.expires_at == T0 + timedelta(seconds=10)


def test_authority_window_never_outlives_any_supporting_item():
    items = b.good_items(req_inflow={"age": 3}, req_liquidity={"age": 17}, req_price={"age": 9})
    d = b.decide(items)
    for it in items:
        assert d.authority_window.expires_at <= it.temporal.observed_at + timedelta(seconds=30)


def test_per_requirement_max_age_drives_window():
    c = b.with_requirement(b.make_contract(), "req_price", max_age_seconds=8)
    d = b.decide(b.good_items(req_price={"age": 5}), c)
    assert d.authority_window.expires_at == T0 + timedelta(seconds=3)


def test_no_window_for_non_authorize():
    for items in (b.good_items()[:2], b.good_items(req_liquidity={"age": 99}), b.good_items(req_liquidity={"value": Decimal(1)})):
        assert b.decide(items).authority_window is None


def test_decision_model_forbids_window_on_non_authorize():
    d = b.decide(b.good_items(req_liquidity={"value": Decimal(1)}))
    good = b.decide()
    data = d.model_dump()
    data["authority_window"] = good.authority_window.model_dump()
    with pytest.raises(ValueError):
        Decision.model_validate(data)
    data = good.model_dump()
    data["authority_window"] = None
    with pytest.raises(ValueError):
        Decision.model_validate(data)


# =============================================================================================
# Contract validation
# =============================================================================================


@pytest.mark.parametrize(
    "field", ["missing_required", "stale_required", "critical_conflict", "failed_threshold"]
)
def test_policy_mapping_cannot_be_authorize(field):
    c = b.make_contract(policy={"minimum_required_evidence": 3, field: "AUTHORIZE"})
    with pytest.raises(InvalidContractError) as exc:
        evaluate(c, b.make_set(b.good_items()), T0)
    assert any(field in p and "AUTHORIZE" in p for p in exc.value.problems)


def test_policy_mapping_to_other_blocking_states_is_honoured():
    c = b.make_contract(policy={"minimum_required_evidence": 3, "stale_required": "ABSTAIN", "failed_threshold": "ESCALATE"})
    assert b.decide(b.good_items(req_liquidity={"age": 60}), c).state == AB
    assert b.decide(b.good_items(req_liquidity={"value": Decimal(1)}), c).state == E


def _req(**kw):
    base = {"id": "r1", "claim": "available_liquidity", "operator": ">", "threshold": Decimal(1), "unit": "USD"}
    base.update(kw)
    return base


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"requirements": []}, "at least one requirement"),
        ({"requirements": [_req(), _req()]}, "unique"),
        ({"requirements": [_req(claim="moon_phase")]}, "unknown claim"),
        ({"requirements": [_req(operator="IN", values=["a"])]}, "not allowed"),
        ({"requirements": [_req(unit="EUR")]}, "unit must be USD"),
        ({"requirements": [_req(threshold=None)]}, "finite threshold"),
        ({"requirements": [_req(claim="venue_id", operator="IN", unit="ID", threshold=None, values=[])]}, "non-empty"),
        ({"requirements": [_req(claim="asset_listed", operator="==", unit="BOOL", threshold=None)]}, "expected value"),
        ({"requirements": [_req(max_age_seconds=0)]}, "max_age_seconds"),
        ({"requirements": [_req(max_age_seconds=86_401)]}, "max_age_seconds"),
        ({"requirements": [_req(minimum_items=0)]}, "minimum_items"),
        ({"requirements": [_req(conflict_tolerance=Decimal("-0.1"))]}, "conflict_tolerance"),
        ({"requirements": [_req(conflict_tolerance=Decimal("1.5"))]}, "conflict_tolerance"),
        ({"policy": {"minimum_required_evidence": -1}}, "minimum_required_evidence"),
        ({"policy": {"clock_skew_seconds": 31}}, "clock_skew_seconds"),
        ({"action": {"type": "BUY", "asset": "TOKEN-X", "max_amount": {"value": Decimal(0)}}}, "positive"),
        ({"action": {"type": "BUY", "asset": "TOKEN-X", "max_amount": {"value": Decimal(101)}}}, "exceeds"),
        ({"economic_limits": {"max_execution_value_usd": Decimal(100), "max_evidence_cost_usd": Decimal(-1)}}, "max_evidence_cost_usd"),
        ({"authorization": {"ttl_seconds": 0}}, "ttl_seconds"),
        ({"authorization": {"ttl_seconds": 601}}, "ttl_seconds"),
    ],
)
def test_contract_validation_errors(overrides, fragment):
    c = b.make_contract(**overrides)
    with pytest.raises(InvalidContractError) as exc:
        validate_contract(c)
    assert any(fragment in p for p in exc.value.problems), exc.value.problems
    with pytest.raises(InvalidContractError):
        evaluate(c, b.make_set([]), T0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"requirements": [_req(threshold=Decimal("NaN"))]},
        {"requirements": [_req(threshold=Decimal("Infinity"))]},
        {"action": {"type": "BUY", "asset": "TOKEN-X", "max_amount": {"value": Decimal("NaN")}}},
        {"economic_limits": {"max_execution_value_usd": Decimal("Infinity")}},
    ],
)
def test_non_finite_contract_numbers_are_rejected_at_the_schema_boundary(overrides):
    with pytest.raises(ValueError):  # pydantic ValidationError is a ValueError
        b.make_contract(**overrides)


def test_valid_contract_passes_validation():
    validate_contract(b.make_contract())
    validate_contract(b.make_contract(authorization={"ttl_seconds": 600}))


def test_asset_is_normalized_and_required():
    c = b.make_contract(action={"type": "BUY", "asset": " token-x ", "max_amount": {"value": Decimal(100)}})
    assert c.action.asset == "TOKEN-X"
    with pytest.raises(ValueError):
        b.make_contract(action={"type": "BUY", "asset": "  ", "max_amount": {"value": Decimal(100)}})


def test_unsupported_engine_version():
    with pytest.raises(UnsupportedEngineVersion):
        evaluate(b.make_contract(), b.make_set(b.good_items()), T0, engine_version="0.9.0")


# =============================================================================================
# Purity & determinism
# =============================================================================================


def test_evaluate_twice_is_identical():
    items = b.good_items()
    c = b.make_contract()
    d1 = evaluate(c, b.make_set(items), T0)
    d2 = evaluate(c, b.make_set(items), T0)
    assert d1.model_dump() == d2.model_dump()


def test_evaluate_does_not_mutate_inputs():
    c = b.make_contract()
    evs = b.make_set(b.good_items() + [b.make_item("req_liquidity", Decimal(91_000), evidence_id="x", provider="x")])
    before = (c.model_dump(), evs.model_dump())
    evaluate(c, evs, T0)
    assert (c.model_dump(), evs.model_dump()) == before


_REAL_DATETIME = datetime


class _TransparentMeta(type):
    """Keeps ``isinstance(x, datetime)`` working for real datetimes while the name is patched."""

    def __instancecheck__(cls, instance):
        return isinstance(instance, _REAL_DATETIME)

    def __subclasscheck__(cls, subclass):
        return issubclass(subclass, _REAL_DATETIME)


def _clock_read(*_a, **_k):
    raise AssertionError("evaluate() read the clock")


class _ExplodingDatetime(datetime, metaclass=_TransparentMeta):
    now = classmethod(_clock_read)
    utcnow = classmethod(_clock_read)
    today = classmethod(_clock_read)
    fromtimestamp = classmethod(_clock_read)
    utcfromtimestamp = classmethod(_clock_read)


CLOCK_FUNCTIONS = {"now", "utcnow", "today", "time", "time_ns", "monotonic", "monotonic_ns", "perf_counter",
                   "perf_counter_ns", "localtime", "gmtime", "urandom", "random", "getrandbits"}


def test_evaluate_does_not_read_the_clock(monkeypatch):
    """(1) every engine module's ``datetime`` and the ``time`` functions raise when called;
    (2) a profiler hook records any C-level clock/randomness call made during evaluate()."""
    import sys
    import time

    from sufficia.canonical import canonicalize, hashes
    from sufficia.decision_engine import authorization, engine, explain as explain_mod, validator

    for mod in (engine, validator, rules, explain_mod, authorization, canonicalize, hashes):
        if getattr(mod, "datetime", None) is datetime:
            monkeypatch.setattr(mod, "datetime", _ExplodingDatetime)
    for name in ("time", "time_ns", "monotonic", "perf_counter", "localtime", "gmtime"):
        monkeypatch.setattr(time, name, _clock_read)

    contract = b.make_contract()
    fresh_set, stale_set = b.make_set(b.good_items()), b.make_set(b.good_items(req_liquidity={"age": 99}))
    seen: list[str] = []

    def profiler(frame, event, arg):
        if event == "c_call" and getattr(arg, "__name__", "") in CLOCK_FUNCTIONS:
            seen.append(f"{getattr(arg, '__self__', None)}.{arg.__name__}")

    sys.setprofile(profiler)
    try:
        d1 = evaluate(contract, fresh_set, T0)
        d2 = evaluate(contract, stale_set, T0)
    finally:
        sys.setprofile(None)
    assert seen == []
    assert d1.state == A and d2.state == R


def test_evaluate_output_depends_only_on_evaluation_time_argument():
    items = b.good_items()
    early = b.decide(items, t=T0)
    late = b.decide(items, t=T0 + timedelta(seconds=40))
    assert early.state == A and late.state == R
    assert early.fingerprint != late.fingerprint and early.evidence_root == late.evidence_root


# =============================================================================================
# Requirement results & metrics
# =============================================================================================


def test_requirement_results_checks():
    d = b.decide(b.good_items(req_liquidity={"value": Decimal(92_440)}))
    rr = {r.requirement_id: r for r in d.requirement_results}
    liq = rr["req_liquidity"]
    assert liq.status == RequirementStatus.FAIL and liq.reason_code.value == "THRESHOLD_NOT_MET"
    assert liq.checks.present and liq.checks.fresh and liq.checks.valid_type and liq.checks.provenance
    assert liq.checks.threshold is False and liq.checks.conflict is False
    assert liq.observed_values == ["92440"] and liq.required_expression == "> $500,000"
    assert rr["req_price"].checks.threshold is True and rr["req_price"].status == RequirementStatus.PASS


def test_requirement_results_flag_invalid_and_provenance():
    d = b.decide(b.good_items(req_liquidity={"response_hash": None}))
    liq = [r for r in d.requirement_results if r.requirement_id == "req_liquidity"][0]
    assert liq.checks.present is False and liq.checks.provenance is False and liq.checks.valid_type is False


# =============================================================================================
# Explanation fidelity for each state
# =============================================================================================


def _explained(d, items=None, contract=None, t=T0, failures=None):
    contract = contract or b.make_contract()
    items = b.good_items() if items is None else items
    chk = check_explanations(contract, b.make_set(items, failures), t, d)
    assert chk.issues == [], chk.issues
    return chk


def test_explanation_authorize():
    d = b.decide()
    _explained(d)
    assert d.reasons[0].message == "Every mandatory evidence requirement is satisfied."
    assert d.headline == "Execution authorized"
    assert d.summary.endswith("may be issued for 100 USDC.")


def test_explanation_deny_contains_observed_and_required_values():
    items = b.good_items(req_liquidity={"value": Decimal(92_440)})
    d = b.decide(items)
    _explained(d, items)
    assert d.reasons[0].message == "Available liquidity is $92,440. The Evidence Contract requires more than $500,000."
    assert d.reasons[0].observed == ["92440"] and d.reasons[0].required == "> $500,000"
    assert d.headline == "Execution denied" and "100 USDC did not move" in d.summary


def test_explanation_refresh_contains_age_and_max_age():
    items = b.good_items(req_liquidity={"age": 92})
    d = b.decide(items)
    _explained(d, items)
    assert d.reasons[0].message == ("The available liquidity observation is 92 seconds old. "
                                    "The Evidence Contract allows a maximum age of 30 seconds.")
    assert d.headline == "Evidence expired"


def test_explanation_escalate_names_both_sources():
    items = b.good_items(req_liquidity={"evidence_id": "a", "provider": "dex"})
    items.append(b.make_item("req_liquidity", Decimal(91_000), evidence_id="b", provider="pool"))
    d = b.decide(items)
    _explained(d, items)
    msg = d.reasons[0].message
    assert "$718,000 (dex)" in msg and "$91,000 (pool)" in msg and "87.326% apart" in msg and "tolerance is 10%" in msg
    assert d.headline == "Human review required"


def test_explanation_abstain_names_cause():
    items = [b.good_items()[0], b.good_items()[2]]
    failures = [{"requirement_id": "req_liquidity", "provider": "dex", "code": "SOURCE_UNAVAILABLE"}]
    d = b.decide(items, failures=failures)
    _explained(d, items, failures=failures)
    assert d.reasons[0].message == ("Required available liquidity evidence could not be established: "
                                    "the source was unavailable.")
    assert d.headline == "Agent abstained" and "does not know enough" in d.summary


def test_explanation_budget_and_insufficient():
    items = b.good_items(req_inflow={"cost": "0.2"}, req_liquidity={"cost": "0.1"})
    d = b.decide(items)
    _explained(d, items)
    c4 = b.make_contract(policy={"minimum_required_evidence": 4})
    d2 = b.decide(contract=c4)
    _explained(d2, contract=c4)


def test_explanation_prohibited_evidence():
    c = single("sanctions_flag", "NOT_EXISTS", unit="BOOL")
    items = [_bool_item(True)]
    d = evaluate(c, b.make_set(items), T0)
    _explained(d, items, contract=c)


def test_explanation_checker_detects_tampered_messages():
    items = b.good_items(req_liquidity={"value": Decimal(92_440)})
    d = b.decide(items)
    reasons = [r.model_copy(update={"message": "Market conditions are risky."}) for r in d.reasons]
    tampered = d.model_copy(update={"reasons": reasons})
    chk = check_explanations(b.make_contract(), b.make_set(items), T0, tampered)
    assert chk.issues


def test_explain_helpers():
    req = Requirement(id="r", claim="available_liquidity", operator="IN", values=["b", "a"])
    assert explain.required_expression(req) == "IN [a, b]"
    assert explain.requirement_phrase(req) == "one of a, b"
    assert explain.format_value(Decimal("0.428"), "USD") == "$0.428"
    assert explain.format_value(Decimal("1234.5"), "USD") == "$1,234.5"
    assert explain.format_value(Decimal("499999.999"), "USD") == "$499,999.999"
    assert explain.format_value(True, None) == "true"
    assert explain.format_value(Decimal("5"), "ID") == "5"
    assert explain.format_money(Decimal("100.0"), "USDC") == "100 USDC"
    assert explain.seconds(Decimal("92.4")) == "92.4" and explain.seconds(Decimal("30.0001")) == "30.001"
    assert explain.percent(Decimal("0.10004")) == "10.004" and explain.percent(Decimal("0.1")) == "10"
    assert explain.message_for(explain.ReasonCode.UNKNOWN_REQUIREMENT, None, {}) == "Unknown requirement."
