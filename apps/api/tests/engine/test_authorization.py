"""Evidence-bound authorization (INV-01, INV-06..INV-09, Invariant A1; Document 04 §15-21)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

import engine_builders as b
from sufficia.canonical.hashes import authorization_id, authorization_nonce, canonical_action_hash, contract_hash
from sufficia.decision_engine import AuthorityError, build_authorization, unix_seconds_floor, verify_binding
from sufficia.domain.enums import AuthorizationStatus, DecisionState
from sufficia.domain.reasons import ReasonCode


def _authorize(decision=None, contract=None, **action_overrides):
    contract = contract or b.make_contract()
    decision = decision or b.decide(contract=contract)
    action = b.action_for(decision, **action_overrides)
    auth = build_authorization(decision=decision, contract=contract, action=action, executor=b.EXECUTOR,
                               onchain_intent_id=b.INTENT32)
    return auth, decision, action, contract


def test_authorize_decision_produces_bound_authorization():
    auth, d, action, contract = _authorize()
    assert auth.status == AuthorizationStatus.ISSUED
    assert auth.decision_id == d.decision_id and auth.intent_id == d.intent_id
    assert auth.contract_hash == d.contract_hash == contract_hash(contract)
    assert auth.evidence_root == d.evidence_root
    assert auth.action_hash == canonical_action_hash(action)
    assert auth.nonce == authorization_nonce(d.fingerprint)
    assert auth.authorization_id == authorization_id(d.contract_hash, d.evidence_root, auth.action_hash, b.EXECUTOR, auth.nonce)
    assert auth.issued_at == d.authority_window.issued_at and auth.expires_at == d.authority_window.expires_at
    assert auth.max_amount.value == Decimal(100) and auth.asset == "TOKEN-X"
    assert auth.onchain_intent_id == b.INTENT32
    assert verify_binding(auth, d, action) == []


def test_authorization_is_deterministic():
    a1, *_ = _authorize()
    a2, *_ = _authorize()
    assert a1.model_dump() == a2.model_dump()


NON_AUTHORIZE = {
    DecisionState.DENY: dict(items=lambda: b.good_items(req_liquidity={"value": Decimal(92_440)})),
    DecisionState.ABSTAIN: dict(items=lambda: b.good_items()[:2]),
    DecisionState.REFRESH: dict(items=lambda: b.good_items(req_liquidity={"age": 92})),
    DecisionState.ESCALATE: dict(items=lambda: b.good_items(req_liquidity={"evidence_id": "a", "provider": "a"})
                                 + [b.make_item("req_liquidity", Decimal(91_000), evidence_id="bb", provider="bb")]),
}


@pytest.mark.parametrize("state", list(NON_AUTHORIZE))
def test_non_authorize_decisions_are_refused(state):
    d = b.decide(NON_AUTHORIZE[state]["items"]())
    assert d.state == state
    action = b.action_for(d, deadline=unix_seconds_floor(b.T0) + 10)
    with pytest.raises(AuthorityError) as exc:
        build_authorization(decision=d, contract=b.make_contract(), action=action, executor=b.EXECUTOR,
                            onchain_intent_id=b.INTENT32)
    assert exc.value.code == ReasonCode.EVIDENCE_AUTHORIZATION_REQUIRED
    assert state.value in str(exc.value)


def test_forged_authorize_state_without_window_is_refused():
    d = b.decide(b.good_items(req_liquidity={"value": Decimal(1)}))
    forged = d.model_construct(**{**dict(d), "state": DecisionState.AUTHORIZE, "authority_window": None})
    with pytest.raises(AuthorityError) as exc:
        build_authorization(decision=forged, contract=b.make_contract(), action=b.action_for(d), executor=b.EXECUTOR,
                            onchain_intent_id=b.INTENT32)
    assert exc.value.code == ReasonCode.EVIDENCE_AUTHORIZATION_REQUIRED


def test_decision_for_another_contract_is_refused():
    d = b.decide()
    other = b.make_contract(contract_id="ec_other")
    with pytest.raises(AuthorityError) as exc:
        build_authorization(decision=d, contract=other, action=b.action_for(d), executor=b.EXECUTOR, onchain_intent_id=b.INTENT32)
    assert exc.value.code == ReasonCode.CONTRACT_MISMATCH


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": {"value": Decimal("100.000001"), "currency": "USDC"}},
        {"amount": {"value": Decimal(1000), "currency": "USDC"}},
        {"amount": {"value": Decimal(100), "currency": "USDT"}},
        {"amount": {"value": Decimal(0), "currency": "USDC"}},
        {"amount": {"value": Decimal(-1), "currency": "USDC"}},
        {"asset": "TOKEN-Y"},
        {"action_type": "SELL"},
        {"action_type": "TRANSFER"},
    ],
)
def test_authority_expansion_is_refused(overrides):
    d = b.decide()
    with pytest.raises(AuthorityError) as exc:
        _authorize(decision=d, **overrides)
    assert exc.value.code == ReasonCode.AUTHORITY_EXPANSION


def test_smaller_amount_and_lowercase_asset_are_allowed():
    auth, *_ = _authorize(amount={"value": Decimal(50), "currency": "USDC"}, asset="token-x")
    assert auth.max_amount.value == Decimal(50)


def test_deadline_beyond_evidence_expiry_is_refused():
    d = b.decide(b.good_items(req_price={"age": 25}))  # expires T0 + 5 s
    expiry = unix_seconds_floor(d.authority_window.expires_at)
    assert expiry == unix_seconds_floor(b.T0 + timedelta(seconds=5))
    _authorize(decision=d, deadline=expiry)  # at the expiry: fine
    with pytest.raises(AuthorityError) as exc:
        _authorize(decision=d, deadline=expiry + 1)
    assert exc.value.code == ReasonCode.AUTHORITY_EXPANSION


@pytest.mark.parametrize("nonce", ["0x" + "00" * 32, "0x" + "ab" * 32])
def test_wrong_nonce_is_refused(nonce):
    with pytest.raises(AuthorityError) as exc:
        _authorize(nonce=nonce)
    assert exc.value.code == ReasonCode.ACTION_HASH_MISMATCH


def test_nonce_from_another_decision_is_refused():
    other = b.decide(b.good_items(req_price={"age": 6}))
    with pytest.raises(AuthorityError):
        _authorize(nonce=authorization_nonce(other.fingerprint))


def test_nonce_case_is_irrelevant():
    d = b.decide()
    auth, *_ = _authorize(decision=d, nonce=authorization_nonce(d.fingerprint).upper().replace("0X", "0x"))
    assert auth.nonce == authorization_nonce(d.fingerprint)


# ---------------------------------------------------------------------------------------------
# verify_binding detects every mutation
# ---------------------------------------------------------------------------------------------


def test_verify_binding_detects_action_mutations():
    auth, d, action, _ = _authorize()
    for field, value in [
        ("target", "0x" + "99" * 20),
        ("max_amount_in", action.max_amount_in + 1),
        ("min_amount_out", action.min_amount_out - 1),
        ("recipient", "0x" + "98" * 20),
        ("asset_in", "0x" + "97" * 20),
        ("asset_out", "0x" + "96" * 20),
        ("deadline", action.deadline - 1),
        ("chain_id", 1),
        ("gate_address", "0x" + "95" * 20),
        ("value", 1),
        ("data", "0x1234"),
        ("nonce", "0x" + "94" * 32),
    ]:
        mutated = action.model_copy(update={field: value})
        problems = verify_binding(auth, d, mutated)
        assert "action_hash" in problems, field
        assert "authorization_id" in problems, field


def test_verify_binding_detects_authorization_mutations():
    auth, d, action, _ = _authorize()
    cases = {
        "contract_hash": auth.model_copy(update={"contract_hash": "0x" + "11" * 32}),
        "evidence_root": auth.model_copy(update={"evidence_root": "0x" + "22" * 32}),
        "action_hash": auth.model_copy(update={"action_hash": "0x" + "33" * 32}),
        "authorization_id": auth.model_copy(update={"authorization_id": "0x" + "44" * 32}),
        "nonce": auth.model_copy(update={"nonce": "0x" + "55" * 32}),
    }
    for expected, mutated in cases.items():
        assert expected in verify_binding(mutated, d, action), expected
    wrong_executor = auth.model_copy(update={"executor": "0x" + "12" * 20})
    assert "authorization_id" in verify_binding(wrong_executor, d, action)


def test_verify_binding_detects_decision_substitution():
    auth, d, action, _ = _authorize()
    other_evidence = b.decide(b.good_items(req_liquidity={"value": Decimal(718_001)}))
    problems = verify_binding(auth, other_evidence, action)
    assert "evidence_root" in problems and "nonce" in problems
    other_contract = b.decide(contract=b.with_requirement(b.make_contract(), "req_liquidity", threshold=Decimal(50_000)))
    assert "contract_hash" in verify_binding(auth, other_contract, action)
    denied = b.decide(b.good_items(req_liquidity={"value": Decimal(1)}))
    assert "decision_not_authorize" in verify_binding(auth, denied, action)


def test_single_mutations_of_the_evidence_break_the_binding():
    """Receipt tamper test (Document 06 §61): $718,000 -> $718,001 changes the evidence root."""
    auth, d, action, contract = _authorize()
    tampered = b.decide(b.good_items(req_liquidity={"value": Decimal(718_001)}), contract)
    assert tampered.state == DecisionState.AUTHORIZE
    assert tampered.evidence_root != auth.evidence_root
    assert verify_binding(auth, tampered, action) != []
