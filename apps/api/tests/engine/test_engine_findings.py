"""Regression tests for engine findings F1-F10 (found by the benchmark review, fixed in the engine).

  F1  exact canonical encoding of high-precision decimals (no 28-digit rounding, no hash collision)
  F2  strings are byte-exact in canonical form (no case folding -> replay cannot flip a decision)
  F3  pathological numbers are INVALID_VALUE, never a crash
  F4  negative / non-finite costs cannot hide budget exhaustion
  F5  raw spend (approval / declared spend) is bounded by the contract maximum (Invariant A1)
  F6  the decision fingerprint commits to acquisition failures and permitted sources
  F7  one evidence_id carrying two different observations makes both unusable (no hash-order winner)
  F8  independence: shared family OR shared upstream is one source (union-find)
  F9  AUTHORIZE always carries a committable window (>= 1 whole second, expires_at > evaluated_at)
  F10 explanations never display a violating value as the boundary it violates
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

import engine_builders as b
from sufficia.benchmark.runner import make_snapshot, replay_snapshot
from sufficia.canonical.canonicalize import canonical_json, loads
from sufficia.canonical.hashes import contract_hash, evidence_item_hash
from sufficia.decision_engine import AuthorityError, build_authorization, evaluate, unix_seconds_floor
from sufficia.domain.enums import DecisionState, EvidenceStatus
from sufficia.domain.models import EvidenceContract, EvidenceSet
from sufficia.domain.reasons import ReasonCode

T0 = b.T0
A = DecisionState.AUTHORIZE


def _replay(contract, items, t=T0):
    evs = b.make_set(items, contract_id=contract.contract_id)
    original = evaluate(contract, evs, t)
    replayed = replay_snapshot(make_snapshot(contract, evs, t, original.engine_version))
    return original, replayed


# ---------------------------------------------------------------------------------------------
# F1 — canonical encoding is not injective beyond 28 significant digits (replay + hash collision)
# ---------------------------------------------------------------------------------------------


def test_f1_high_precision_values_replay_identically():
    value = Decimal("500000.00000000000000000000001")  # 29 significant digits, > 500000
    original, replayed = _replay(b.make_contract(), b.good_items(req_liquidity={"value": value}))
    assert original.state == A
    assert replayed.state == original.state  # replay says DENY with the SAME fingerprint


def test_f1_distinct_values_have_distinct_commitments():
    c1 = b.make_contract()
    c2 = b.with_requirement(c1, "req_liquidity", threshold=Decimal("500000.00000000000000000000001"))
    assert contract_hash(c1) != contract_hash(c2)


# ---------------------------------------------------------------------------------------------
# F2 — canonicalization lower-cases 0x+40-hex *values*, but the engine compares them case-sensitively
# ---------------------------------------------------------------------------------------------


def test_f2_address_valued_claims_replay_identically():
    venue = "0xAbCdEf0123456789aBcDeF0123456789AbCdEf01"
    c = b.make_contract(requirements=[{"id": "r1", "claim": "venue_id", "operator": "IN", "values": [venue], "unit": "ID"}],
                        policy={"minimum_required_evidence": 1})
    item = b.make_item("r1", venue.lower(), claim="venue_id", unit="ID", evidence_id="v")
    original, replayed = _replay(c, [item])
    assert original.fingerprint == replayed.fingerprint  # same commitments...
    assert original.state == replayed.state  # ...but DENY originally, AUTHORIZE on replay


# ---------------------------------------------------------------------------------------------
# F3 — pathological numbers crash evaluate() instead of producing INVALID_VALUE
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [10**5000, Decimal("1E+5000000")], ids=["int-5001-digits", "decimal-1e5000000"])
def test_f3_overflow_sized_numbers_are_invalid_value(value):
    d = b.decide(b.good_items(req_liquidity={"value": value}))
    assert d.state == DecisionState.ABSTAIN
    assert [c.value for c in d.reasons[0].causes] == ["INVALID_VALUE"]


# ---------------------------------------------------------------------------------------------
# F4 — negative costs are accepted and offset real spend (budget bypass)
# ---------------------------------------------------------------------------------------------


def test_f4_negative_cost_cannot_hide_budget_exhaustion():
    items = b.good_items(req_inflow={"cost": "0.20"}, req_liquidity={"cost": "0.20"})  # 0.40 > 0.25
    junk = b.make_item("req_nonexistent", Decimal(1), claim="available_liquidity", evidence_id="zz_junk", cost="-0.30")
    d = b.decide(items + [junk])
    assert b.assessment(d, "zz_junk").status == EvidenceStatus.INVALID
    assert d.state == DecisionState.ABSTAIN and d.reason_codes == [ReasonCode.EVIDENCE_BUDGET_EXHAUSTED]


def test_f4_negative_failure_cost_cannot_hide_budget_exhaustion():
    items = b.good_items(req_inflow={"cost": "0.20"}, req_liquidity={"cost": "0.20"})
    failures = [{"requirement_id": "req_price", "provider": "x", "code": "RATE_LIMITED", "cost_spent": Decimal("-0.30")}]
    assert b.decide(items, failures=failures).state == DecisionState.ABSTAIN


# ---------------------------------------------------------------------------------------------
# F5 — build_authorization bounds the display amount, not the raw amount EvidenceGate spends
# ---------------------------------------------------------------------------------------------


def test_f5_raw_spend_cannot_exceed_contract_maximum():
    d = b.decide()
    action = b.action_for(d, max_amount_in=10**12)  # 1,000,000 USDC (6 decimals) while amount.value == 100
    with pytest.raises(AuthorityError) as exc:
        build_authorization(decision=d, contract=b.make_contract(), action=action, executor=b.EXECUTOR,
                            onchain_intent_id=b.INTENT32)
    assert exc.value.code == ReasonCode.AUTHORITY_EXPANSION


# ---------------------------------------------------------------------------------------------
# F6 — the decision fingerprint does not commit to every decision input
# ---------------------------------------------------------------------------------------------


def test_f6_different_decisions_have_different_ids_acquisition_failures():
    base = b.decide()
    failures = [{"requirement_id": "req_liquidity", "provider": "x", "code": "PAYMENT_FAILED", "cost_spent": Decimal("1")}]
    other = b.decide(failures=failures)
    assert (base.state, other.state) == (A, DecisionState.ABSTAIN)
    assert base.decision_id != other.decision_id and base.fingerprint != other.fingerprint


def test_f6_different_decisions_have_different_ids_permitted_sources():
    c, evs = b.make_contract(), b.make_set(b.good_items())
    d1, d2 = evaluate(c, evs, T0), evaluate(c, evs, T0, permitted_sources={})
    assert (d1.state, d2.state) == (A, DecisionState.ABSTAIN)
    assert d1.decision_id != d2.decision_id


# ---------------------------------------------------------------------------------------------
# F7 — two different observations sharing an evidence_id: one is silently discarded
# ---------------------------------------------------------------------------------------------


def test_f7_reused_evidence_id_with_different_content_is_not_a_silent_duplicate():
    good = b.make_item("req_liquidity", Decimal(718_000), evidence_id="ev_liq")
    bad = b.make_item("req_liquidity", Decimal(92_440), evidence_id="ev_liq", provider="pool")
    d = b.decide([b.make_item("req_inflow"), good, bad, b.make_item("req_price")])
    discarded = [a for a in d.evidence_assessments if a.status == EvidenceStatus.DUPLICATE]
    assert not discarded  # the two items are not the same observation; neither may be dropped silently


def test_f7_outcome_is_independent_of_hash_order():
    """Whatever the colliding value, both copies are INVALID (EVIDENCE_ID_COLLISION) and the case abstains."""
    outcomes = set()
    for v in range(1, 40):
        good = b.make_item("req_liquidity", Decimal(718_000), evidence_id="ev_liq")
        bad = b.make_item("req_liquidity", Decimal(v), evidence_id="ev_liq", provider="pool")
        d = b.decide([b.make_item("req_inflow"), good, bad, b.make_item("req_price")])
        outcomes.add(d.state)
        collided = [a for a in d.evidence_assessments if a.evidence_id == "ev_liq"]
        assert len(collided) == 2 and all(ReasonCode.EVIDENCE_ID_COLLISION in a.reason_codes for a in collided)
    assert outcomes == {DecisionState.ABSTAIN}


# ---------------------------------------------------------------------------------------------
# F8 — independence: a declared source_family hides a shared upstream oracle
# ---------------------------------------------------------------------------------------------


def test_f8_shared_upstream_is_one_source_even_with_distinct_families():
    items = [b.make_item(rid, provider=f"api-{i}", source_family=f"family-{i}", upstream_source="chainlink:TOKENX/USD")
             for i, rid in enumerate(b.REQUIREMENTS)]
    assert b.decide(items).independent_source_count == 1


# ---------------------------------------------------------------------------------------------
# F9 — AUTHORIZE with an unusable authority window
# ---------------------------------------------------------------------------------------------


def test_f9_authorize_window_is_never_empty():
    d = b.decide(b.good_items(req_liquidity={"age": 30}))
    assert d.state != A or d.authority_window.expires_at > d.evaluated_at


def test_f9_authorize_window_is_committable_onchain():
    for age in (Decimal("29.999"), Decimal("29.5"), Decimal("30")):
        d = b.decide(b.good_items(req_liquidity={"age": age}))
        assert d.state == DecisionState.REFRESH and d.reason_codes == [ReasonCode.AUTHORITY_WINDOW_EXHAUSTED]
        assert d.authority_window is None
    d = b.decide(b.good_items(req_liquidity={"age": Decimal("29")}))
    assert d.state == A
    w = d.authority_window
    assert unix_seconds_floor(w.expires_at) > unix_seconds_floor(w.issued_at)


# ---------------------------------------------------------------------------------------------
# F10 — explanation display precision hides the violated boundary (INV-14)
# ---------------------------------------------------------------------------------------------


def test_f10_stale_message_shows_the_violation():
    d = b.decide(b.good_items(req_liquidity={"age": Decimal("30.001")}))
    assert d.state == DecisionState.REFRESH
    assert "30 seconds old" not in d.reasons[0].message


def test_f10_threshold_message_shows_the_violation():
    c = b.with_requirement(b.make_contract(), "req_liquidity", operator=">=")
    d = b.decide(b.good_items(req_liquidity={"value": Decimal("499999.999")}), c)
    assert d.state == DecisionState.DENY
    assert "$500,000.00" not in d.reasons[0].message


def test_f10_conflict_message_shows_the_violation():
    items = b.good_items(req_liquidity={"value": Decimal(1_000_000), "evidence_id": "a", "provider": "a"})
    items.append(b.make_item("req_liquidity", Decimal(899_960), evidence_id="bb", provider="bb"))
    d = b.decide(items)
    assert d.state == DecisionState.ESCALATE
    assert "10% apart" not in d.reasons[0].message
