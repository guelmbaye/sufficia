"""Property-based invariants (Document 03 §101-102): P1-P6, A15 monotonic safety, permutation
determinism. Hypothesis runs derandomized with no example database, so no .hypothesis directory is
created and every run explores the same examples."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import engine_builders as b
from sufficia.canonical.hashes import canonical_action_hash, contract_hash
from sufficia.decision_engine import AuthorityError, build_authorization, evaluate
from sufficia.domain.enums import DecisionState, Operator
from sufficia.domain.models import CanonicalAction

PROFILE = settings(
    database=None,
    derandomize=True,
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

A = DecisionState.AUTHORIZE
T0 = b.T0
RIDS = list(b.REQUIREMENTS)

# ---------------------------------------------------------------------------------------------
# strategies
# ---------------------------------------------------------------------------------------------

ages = st.decimals(min_value=0, max_value=30, places=3)
inflow_pass = st.decimals(min_value=Decimal("1000000.01"), max_value=Decimal("1e12"), places=2)
liquidity_pass = st.decimals(min_value=Decimal("500000.01"), max_value=Decimal("1e12"), places=2)
price_any = st.decimals(min_value=0, max_value=Decimal("1e6"), places=4)


@st.composite
def authorizing_worlds(draw):
    """A contract (possibly with random thresholds/ages) plus evidence that AUTHORIZEs by construction."""
    ttl = draw(st.integers(min_value=1, max_value=600))
    max_ages = {rid: draw(st.integers(min_value=1, max_value=600)) for rid in RIDS}
    t_inflow = draw(st.decimals(min_value=0, max_value=Decimal("1e9"), places=2))
    t_liq = draw(st.decimals(min_value=0, max_value=Decimal("1e9"), places=2))
    contract = b.make_contract(authorization={"ttl_seconds": ttl})
    contract = b.with_requirement(contract, "req_inflow", threshold=t_inflow, max_age_seconds=max_ages["req_inflow"])
    contract = b.with_requirement(contract, "req_liquidity", threshold=t_liq, max_age_seconds=max_ages["req_liquidity"])
    contract = b.with_requirement(contract, "req_price", max_age_seconds=max_ages["req_price"])
    values = {
        "req_inflow": t_inflow + draw(st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1e9"), places=2)),
        "req_liquidity": t_liq + draw(st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1e9"), places=2)),
        "req_price": draw(price_any),
    }
    item_ages = {rid: draw(st.decimals(min_value=0, max_value=max_ages[rid] - 1, places=3)) for rid in RIDS}  # >= 1 s of authority
    items = [b.make_item(rid, values[rid], age=item_ages[rid]) for rid in RIDS]
    # optional extra non-conflicting liquidity observation from another provider
    if draw(st.booleans()):
        factor = draw(st.decimals(min_value=Decimal("1.00"), max_value=Decimal("1.10"), places=2))
        extra_value = values["req_liquidity"] * factor
        age = draw(st.decimals(min_value=0, max_value=max_ages["req_liquidity"] - 1, places=3))
        items.append(b.make_item("req_liquidity", extra_value, evidence_id="ev_liq_extra", provider="pool-reader", age=age))
    return contract, items


def _eval(contract, items, t=T0):
    return evaluate(contract, b.make_set(items, contract_id=contract.contract_id), t)


def _mandatory_items(items, rid):
    return [i for i in items if i.requirement_id == rid]


# ---------------------------------------------------------------------------------------------
# sanity: the generator produces AUTHORIZE worlds
# ---------------------------------------------------------------------------------------------


@PROFILE
@given(authorizing_worlds())
def test_generator_produces_authorize(world):
    contract, items = world
    d = _eval(contract, items)
    assert d.state == A, d.reasons
    # INV-09 for every generated world
    for it in items:
        req = contract.requirement(it.requirement_id)
        assert d.authority_window.expires_at <= it.temporal.observed_at + timedelta(seconds=req.max_age_seconds)
    assert d.authority_window.expires_at <= T0 + timedelta(seconds=contract.authorization.ttl_seconds)


# ---------------------------------------------------------------------------------------------
# P1 — adding a missing mandatory requirement cannot preserve AUTHORIZE
# ---------------------------------------------------------------------------------------------

NEW_REQUIREMENTS = [
    {"id": "req_zz_listed", "claim": "asset_listed", "operator": "==", "expected": True, "unit": "BOOL"},
    {"id": "req_zz_venue", "claim": "venue_id", "operator": "IN", "values": ["kuru"], "unit": "ID"},
    {"id": "req_aa_price_cap", "claim": "asset_price", "operator": "<", "threshold": Decimal(10), "unit": "USD"},
    {"id": "req_mm_liq2", "claim": "available_liquidity", "operator": ">=", "threshold": Decimal(1), "unit": "USD"},
]


@PROFILE
@given(authorizing_worlds(), st.sampled_from(NEW_REQUIREMENTS))
def test_p1_adding_unsupported_mandatory_requirement_breaks_authorize(world, new_req):
    contract, items = world
    d = contract.model_dump(mode="python")
    d["requirements"].append(new_req)
    stricter = type(contract).model_validate(d)
    decision = _eval(stricter, items)
    assert decision.state != A
    assert decision.state == DecisionState.ABSTAIN
    assert new_req["id"] in [r.requirement_id for r in decision.reasons]


# ---------------------------------------------------------------------------------------------
# P2 — making valid evidence stale cannot preserve AUTHORIZE
# ---------------------------------------------------------------------------------------------


@PROFILE
@given(authorizing_worlds(), st.sampled_from(RIDS), st.decimals(min_value=Decimal("0.001"), max_value=Decimal(86_400), places=3))
def test_p2_staling_all_evidence_of_a_requirement_breaks_authorize(world, rid, extra):
    contract, items = world
    max_age = contract.requirement(rid).max_age_seconds
    stale_at = T0 - timedelta(seconds=max_age) - timedelta(microseconds=int(extra * 1_000_000))
    mutated = [i.model_copy(update={"temporal": i.temporal.model_copy(update={"observed_at": stale_at})})
               if i.requirement_id == rid else i for i in items]
    d = _eval(contract, mutated)
    assert d.state != A
    assert d.state == DecisionState.REFRESH


@PROFILE
@given(authorizing_worlds(), st.sampled_from(RIDS), st.decimals(min_value=Decimal("0.001"), max_value=Decimal(3600), places=3))
def test_p2_advancing_evaluation_time_past_last_support_breaks_authorize(world, rid, extra):
    """Once every observation of one mandatory requirement has expired, AUTHORIZE is impossible.
    (Expiry of only the *earliest* item is not enough: a redundant fresh sibling keeps support.)"""
    contract, items = world
    d0 = _eval(contract, items)
    max_age = timedelta(seconds=contract.requirement(rid).max_age_seconds)
    last_support = max(i.temporal.observed_at + max_age for i in _mandatory_items(items, rid))
    later = last_support + timedelta(microseconds=int(extra * 1_000_000))
    assert d0.authority_window.expires_at <= last_support
    d1 = _eval(contract, items, later)
    assert d1.state == DecisionState.REFRESH


# ---------------------------------------------------------------------------------------------
# P3 — turning a passing threshold into a failing value cannot preserve AUTHORIZE
# ---------------------------------------------------------------------------------------------


@PROFILE
@given(authorizing_worlds(), st.sampled_from(["req_inflow", "req_liquidity"]), st.decimals(min_value=0, max_value=1, places=4))
def test_p3_failing_threshold_breaks_authorize(world, rid, fraction):
    contract, items = world
    threshold = contract.requirement(rid).threshold
    failing_value = (threshold * fraction).quantize(Decimal("0.01"))  # <= threshold, so "> threshold" fails
    target = _mandatory_items(items, rid)[0]
    mutated = [i.model_copy(update={"claim": i.claim.model_copy(update={"value": failing_value})}) if i is target else i
               for i in items]
    d = _eval(contract, mutated)
    assert d.state != A
    # a failing fresh value is DENY, unless it now also conflicts with a sibling observation
    assert d.state in (DecisionState.DENY, DecisionState.ESCALATE)


@PROFILE
@given(authorizing_worlds(), st.sampled_from([Operator.GT, Operator.GTE]), st.decimals(min_value=0, max_value=Decimal("1e9"), places=2))
def test_p3_raising_threshold_above_observation_breaks_authorize(world, op, bump):
    contract, items = world
    highest = max(i.claim.value for i in _mandatory_items(items, "req_liquidity"))
    new_threshold = highest + bump + (Decimal("0.01") if op == Operator.GTE else 0)
    stricter = b.with_requirement(contract, "req_liquidity", operator=op, threshold=new_threshold)
    assert _eval(stricter, items).state == DecisionState.DENY


# ---------------------------------------------------------------------------------------------
# P4 — changing the action changes action_hash
# ---------------------------------------------------------------------------------------------

ADDR = st.binary(min_size=20, max_size=20).map(lambda x: "0x" + x.hex())
U256 = st.integers(min_value=0, max_value=2**256 - 1)
ACTION_FIELDS = {
    "chain_id": st.integers(min_value=1, max_value=2**64),
    "gate_address": ADDR,
    "target": ADDR,
    "value": U256,
    "data": st.binary(max_size=64).map(lambda x: "0x" + x.hex()),
    "asset_in": ADDR,
    "max_amount_in": U256,
    "asset_out": ADDR,
    "min_amount_out": U256,
    "recipient": ADDR,
    "deadline": st.integers(min_value=0, max_value=2**64 - 1),
    "nonce": st.binary(min_size=32, max_size=32).map(lambda x: "0x" + x.hex()),
}


@st.composite
def action_and_mutation(draw):
    base = {k: draw(v) for k, v in ACTION_FIELDS.items()}
    field = draw(st.sampled_from(sorted(ACTION_FIELDS)))
    new_value = draw(ACTION_FIELDS[field].filter(lambda v: str(v).lower() != str(base[field]).lower()))
    return base, field, new_value


@PROFILE
@given(action_and_mutation())
def test_p4_changing_the_action_changes_action_hash(data):
    base, field, new_value = data
    a1 = CanonicalAction.model_validate(b.action_dict(**base))
    a2 = CanonicalAction.model_validate(b.action_dict(**{**base, field: new_value}))
    assert canonical_action_hash(a1) != canonical_action_hash(a2)
    assert canonical_action_hash(a1) == canonical_action_hash(CanonicalAction.model_validate(b.action_dict(**base)))


# ---------------------------------------------------------------------------------------------
# P5 — changing evidence changes evidence_root
# ---------------------------------------------------------------------------------------------

EVIDENCE_MUTATIONS = st.one_of(
    st.tuples(st.just("value"), st.decimals(min_value=-10**9, max_value=10**9, places=2)),
    st.tuples(st.just("age"), st.decimals(min_value=0, max_value=100, places=6)),
    st.tuples(st.just("provider"), st.text(min_size=1, max_size=20)),
    st.tuples(st.just("response_hash"), st.binary(min_size=32, max_size=32).map(lambda x: "0x" + x.hex())),
    st.tuples(st.just("cost"), st.decimals(min_value=0, max_value=1, places=4)),
    st.tuples(st.just("evidence_id"), st.text(min_size=1, max_size=20)),
)


def _mutate(item, field, new):
    """Returns a copy of `item` with one field changed, or None when `new` equals the current value."""
    if field == "value":
        if Decimal(item.claim.value) == new:
            return None
        return item.model_copy(update={"claim": item.claim.model_copy(update={"value": new})})
    if field == "age":
        observed = T0 - timedelta(microseconds=int(new * 1_000_000))
        if observed == item.temporal.observed_at:
            return None
        return item.model_copy(update={"temporal": item.temporal.model_copy(update={"observed_at": observed})})
    if field == "provider":
        if new == item.source.provider:
            return None
        return item.model_copy(update={"source": item.source.model_copy(update={"provider": new})})
    if field == "response_hash":
        if new == item.integrity.response_hash.lower():
            return None
        return item.model_copy(update={"integrity": item.integrity.model_copy(update={"response_hash": new})})
    if field == "cost":
        if Decimal(item.economic.cost) == new:
            return None
        return item.model_copy(update={"economic": item.economic.model_copy(update={"cost": new})})
    if field == "evidence_id":
        return None if new == item.evidence_id else item.model_copy(update={"evidence_id": new})
    raise AssertionError(field)


@PROFILE
@given(authorizing_worlds(), st.integers(min_value=0, max_value=10), EVIDENCE_MUTATIONS)
def test_p5_changing_evidence_changes_evidence_root(world, index, mutation):
    contract, items = world
    i = index % len(items)
    mutated = _mutate(items[i], *mutation)
    if mutated is None:
        return
    new_items = items[:i] + [mutated] + items[i + 1:]
    assert _eval(contract, new_items).evidence_root != _eval(contract, items).evidence_root


@PROFILE
@given(authorizing_worlds())
def test_p5_adding_or_removing_evidence_changes_evidence_root(world):
    contract, items = world
    root = _eval(contract, items).evidence_root
    assert _eval(contract, items[1:]).evidence_root != root
    extra = b.make_item("req_price", Decimal("0.5"), evidence_id="ev_extra", provider="other")
    assert _eval(contract, items + [extra]).evidence_root != root


# ---------------------------------------------------------------------------------------------
# P6 — changing a threshold changes contract_hash
# ---------------------------------------------------------------------------------------------


@PROFILE
@given(
    st.sampled_from(["req_inflow", "req_liquidity"]),
    st.decimals(min_value=-10**12, max_value=10**12, places=6),
    st.decimals(min_value=-10**12, max_value=10**12, places=6),
)
def test_p6_changing_threshold_changes_contract_hash(rid, t1, t2):
    base = b.make_contract()
    h1 = contract_hash(b.with_requirement(base, rid, threshold=t1))
    h2 = contract_hash(b.with_requirement(base, rid, threshold=t2))
    assert (h1 == h2) == (t1 == t2)


@PROFILE
@given(st.integers(min_value=1, max_value=600), st.integers(min_value=1, max_value=86_400), st.sampled_from(RIDS))
def test_p6_ttl_and_max_age_are_committed(ttl, max_age, rid):
    base = b.make_contract()
    c = b.with_requirement(b.make_contract(authorization={"ttl_seconds": ttl}), rid, max_age_seconds=max_age)
    same = (ttl == 30) and (max_age == 30)
    assert (contract_hash(c) == contract_hash(base)) == same


# ---------------------------------------------------------------------------------------------
# A15 — removing any valid mandatory evidence never increases authority (monotonic safety)
# ---------------------------------------------------------------------------------------------


@st.composite
def arbitrary_worlds(draw):
    """Evidence sets that may produce any state; items may be valid, stale, failing, conflicting."""
    contract = b.make_contract(policy={"minimum_required_evidence": draw(st.integers(min_value=0, max_value=4))})
    items = []
    for rid in RIDS:
        for k in range(draw(st.integers(min_value=0, max_value=2))):
            if rid == "req_price":
                value = draw(price_any)
            else:
                value = draw(st.decimals(min_value=0, max_value=Decimal(3_000_000), places=2))
            age = draw(st.decimals(min_value=-1, max_value=60, places=3))
            items.append(b.make_item(rid, value, evidence_id=f"{rid}_{k}", provider=f"p{k}", age=age))
    return contract, items


@PROFILE
@given(authorizing_worlds(), st.data())
def test_a15_removing_mandatory_evidence_from_authorize_never_authorizes(world, data):
    contract, items = world
    subset = data.draw(st.lists(st.sampled_from(range(len(items))), unique=True, min_size=1))
    removed = [it for i, it in enumerate(items) if i not in subset]
    remaining_by_req = {rid: [i for i in removed if i.requirement_id == rid] for rid in RIDS}
    d = _eval(contract, removed)
    full = _eval(contract, items)
    if all(remaining_by_req.values()):
        # every requirement still has a fresh passing item: still authorized, and the authority window
        # can only be bounded by evidence that was also present before
        assert d.state == A
        remaining_ids = {i.evidence_id for i in removed}
        bound = d.authority_window.bounded_by
        assert bound == "CONTRACT_TTL" or bound.split(":", 1)[1] in remaining_ids
        # min() over a subset: the window is still bounded by (fresher) remaining support (INV-09)
        assert d.authority_window.expires_at >= full.authority_window.expires_at
        for it in removed:
            max_age = timedelta(seconds=contract.requirement(it.requirement_id).max_age_seconds)
            assert d.authority_window.expires_at <= it.temporal.observed_at + max_age
    else:
        assert d.state != A


@PROFILE
@given(arbitrary_worlds(), st.data())
def test_a15_monotonic_safety_on_arbitrary_worlds(world, data):
    """Removing one valid item from any world:
      * never turns ABSTAIN or REFRESH into AUTHORIZE (less support never creates authority);
      * never turns AUTHORIZE into DENY or ESCALATE (removal cannot fabricate a failure/conflict);
      * turns DENY/ESCALATE into AUTHORIZE only when the removed item was itself the failing or
        conflicting observation of a requirement that keeps other passing support (see the
        evidence-withholding test below: the resulting decision commits to a different root).
    """
    contract, items = world
    if not items:
        return
    before = _eval(contract, items)
    idx = data.draw(st.integers(min_value=0, max_value=len(items) - 1))
    removed_item = items[idx]
    rest = items[:idx] + items[idx + 1:]
    after = _eval(contract, rest)
    if before.state in (DecisionState.ABSTAIN, DecisionState.REFRESH):
        assert after.state != A
    if before.state == A:
        assert after.state in (A, DecisionState.ABSTAIN, DecisionState.REFRESH)
    if after.state == A and before.state in (DecisionState.DENY, DecisionState.ESCALATE):
        req = contract.requirement(removed_item.requirement_id)
        siblings = [i for i in rest if i.requirement_id == req.id]
        assert siblings, "authority appeared after removing the only evidence of a requirement"
        blamed = {r.requirement_id for r in before.reasons}
        assert req.id in blamed, "removing evidence of an unrelated requirement changed the outcome"
    if after.state == A:
        assert after.evidence_root != before.evidence_root
        for rid in RIDS:
            assert any(i.requirement_id == rid for i in rest)


def test_a15_evidence_withholding_changes_the_commitment():
    """Documented semantics: with minimum_items = 1, a requirement is judged on the observations
    supplied. Withholding the failing observation of a two-source requirement turns DENY into
    AUTHORIZE, but the new decision commits to a different evidence_root, fingerprint and nonce, so
    the DENY decision can never be converted into authority and the receipt shows which set was used."""
    items = b.good_items(req_liquidity={"value": Decimal(510_000)})
    failing = b.make_item("req_liquidity", Decimal(495_000), evidence_id="ev_liq_low", provider="pool")
    denied = b.decide(items + [failing])
    withheld = b.decide(items)
    assert denied.state == DecisionState.DENY and withheld.state == A
    assert denied.evidence_root != withheld.evidence_root
    assert denied.fingerprint != withheld.fingerprint
    two_sources = b.with_requirement(b.make_contract(), "req_liquidity", minimum_items=2)
    assert b.decide(items, two_sources).state == DecisionState.ABSTAIN  # contracts can demand both sources


@PROFILE
@given(authorizing_worlds(), st.sampled_from(RIDS))
def test_a15_removing_all_evidence_of_a_requirement_abstains(world, rid):
    contract, items = world
    d = _eval(contract, [i for i in items if i.requirement_id != rid])
    assert d.state == DecisionState.ABSTAIN
    assert rid in [r.requirement_id for r in d.reasons]


@PROFILE
@given(authorizing_worlds(), st.sampled_from(RIDS))
def test_a15_removed_support_cannot_authorize_an_action(world, rid):
    contract, items = world
    d = _eval(contract, [i for i in items if i.requirement_id != rid])
    action = b.action_for(d, deadline=0)
    try:
        build_authorization(decision=d, contract=contract, action=action, executor=b.EXECUTOR, onchain_intent_id=b.INTENT32)
    except AuthorityError as exc:
        assert exc.code.value == "EVIDENCE_AUTHORIZATION_REQUIRED"
    else:  # pragma: no cover
        raise AssertionError("authorization issued without supporting evidence")


# ---------------------------------------------------------------------------------------------
# Determinism under permutation of evidence order
# ---------------------------------------------------------------------------------------------


@PROFILE
@given(arbitrary_worlds(), st.randoms(use_true_random=False))
def test_permutation_of_evidence_order_is_irrelevant(world, rnd):
    contract, items = world
    shuffled = list(items)
    rnd.shuffle(shuffled)
    d1 = _eval(contract, items)
    d2 = _eval(contract, shuffled)
    assert d1.model_dump() == d2.model_dump()


@PROFILE
@given(arbitrary_worlds(), st.randoms(use_true_random=False))
def test_permutation_of_acquisition_failures_is_irrelevant(world, rnd):
    contract, items = world
    failures = [
        {"requirement_id": "req_liquidity", "provider": "a", "code": "SOURCE_UNAVAILABLE"},
        {"requirement_id": "req_price", "provider": "b", "code": "RATE_LIMITED", "cost_spent": Decimal("0.01")},
        {"requirement_id": "req_inflow", "provider": "c", "code": "PAYMENT_FAILED"},
    ]
    shuffled = list(failures)
    rnd.shuffle(shuffled)
    d1 = evaluate(contract, b.make_set(items, failures), T0)
    d2 = evaluate(contract, b.make_set(items, shuffled), T0)
    assert d1.model_dump() == d2.model_dump()


@PROFILE
@given(arbitrary_worlds())
def test_evaluation_is_a_pure_function(world):
    contract, items = world
    assert _eval(contract, items).model_dump() == _eval(contract, list(items)).model_dump()
