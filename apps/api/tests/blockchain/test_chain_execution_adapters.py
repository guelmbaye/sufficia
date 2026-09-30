"""Execution adapters and verify_spend (pure: synthetic ChainInfo, real Decision Engine)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from eth_abi import encode as abi_encode
from eth_utils import keccak

from sufficia.blockchain.gateway import ChainInfo
from sufficia.canonical.hashes import canonical_action_hash
from sufficia.decision_engine import AuthorityError, build_authorization, evaluate
from sufficia.domain.enums import ActionType, ChainMode, DecisionState
from sufficia.domain.models import EvidenceSet
from sufficia.domain.reasons import ReasonCode
from sufficia.execution import (
    AdapterError,
    DemoSwapAdapter,
    TransferAdapter,
    action_deadline,
    action_nonce,
    get_adapter,
    prepare_action,
    verify_spend,
)
from sufficia.execution.adapters import SWAP_EXACT_IN_SIGNATURE, TRANSFER_SIGNATURE, ZERO_ADDRESS

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
GATE = "0x6E05f58eEddA592f34DD9105b1827f252c509De0"
USDC_ADDR = "0xAE519FC2Ba8e6fFE6473195c092bF1BAe986ff90"
TOKENX_ADDR = "0x73b647cbA2FE75Ba05B8e12ef8F8D6327D6367bF"
ROUTER = "0x7d73424a8256C0b2BA245e5d5a3De8820E45F390"
EXECUTOR = "0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB"
INTENT_ID = "0x" + "ab" * 32


class S:
    """Minimal settings object for adapters."""

    def __init__(self, recipient=None, slippage_bps=100):
        self.execution_recipient = recipient
        self.slippage_bps = slippage_bps


@pytest.fixture
def chain() -> ChainInfo:
    return ChainInfo(
        mode=ChainMode.LOCAL_EVM,
        chain_id=131277322940537,
        network_name="local-evm (py-evm, not Monad)",
        gate_address=GATE,
        usdc_address=USDC_ADDR,
        token_x_address=TOKENX_ADDR,
        router_address=ROUTER,
        explorer_url=None,
        authorizer="0x1563915e194D8CfBA1943570603F7606A3115508",
        executor=EXECUTOR,
    )


def decide(helpers, *, prices=(Decimal("0.428"),), liquidity=Decimal("750000"), **contract_kwargs):
    contract = helpers.make_contract(**contract_kwargs)
    items = helpers.make_evidence(contract, NOW, prices=prices, liquidity=liquidity)
    decision = evaluate(contract, EvidenceSet(contract_id=contract.contract_id, items=items), NOW)
    return contract, items, decision


def swap(helpers, chain, settings=None, **kwargs):
    contract, items, decision = decide(helpers, **kwargs)
    assert decision.state == DecisionState.AUTHORIZE, decision.summary
    action = prepare_action("demo_swap", contract=contract, decision=decision, evidence_items=items, chain=chain, settings=settings or S(helpers.RECIPIENT))
    return contract, items, decision, action


# ---------------------------------------------------------------------------------------------
# DemoSwapAdapter
# ---------------------------------------------------------------------------------------------


def test_demo_swap_min_out_from_fresh_price_evidence(helpers, chain):
    contract, _, decision, action = swap(helpers, chain)
    expected = Decimal(100) / Decimal("0.428") * Decimal(10**18)
    assert action.min_amount_out == math.floor(expected * Decimal(9900) / Decimal(10000))
    assert action.min_amount_out == 231_308_411_214_953_271_028
    assert action.max_amount_in == 100 * helpers.USDC == action.spend_raw
    assert action.amount_decimals == 6
    assert (action.target, action.asset_in, action.asset_out) == (ROUTER, USDC_ADDR, TOKENX_ADDR)
    assert action.recipient == helpers.RECIPIENT and action.value == 0
    assert action.nonce == action_nonce(decision) and action.deadline == action_deadline(decision)
    assert action.adapter == "demo_swap"
    assert action.description == "Swap 100 USDC → ≥231.3 TOKEN-X via DemoSwapRouter"
    calldata = keccak(text=SWAP_EXACT_IN_SIGNATURE)[:4] + abi_encode(
        ["address", "address", "uint256", "uint256", "address"],
        [USDC_ADDR, TOKENX_ADDR, 100 * helpers.USDC, action.min_amount_out, helpers.RECIPIENT],
    )
    assert action.data == "0x" + calldata.hex()
    assert verify_spend(action, chain) == []
    # The action is authorizable by the real backend path.
    auth = build_authorization(decision=decision, contract=contract, action=action, executor=EXECUTOR, onchain_intent_id=INTENT_ID)
    assert auth.action_hash == canonical_action_hash(action)


def test_demo_swap_uses_highest_price_and_slippage(helpers, chain):
    # Two agreeing price sources (within the 10% conflict tolerance): the highest is the conservative one.
    *_, action = swap(helpers, chain, prices=(Decimal("0.42"), Decimal("0.44")))
    assert action.min_amount_out == math.floor(Decimal(100) / Decimal("0.44") * Decimal(10**18) * Decimal("0.99"))
    *_, zero_slip = swap(helpers, chain, settings=S(helpers.RECIPIENT, slippage_bps=0))
    assert zero_slip.min_amount_out == math.floor(Decimal(100) / Decimal("0.428") * Decimal(10**18))


def test_demo_swap_without_price_evidence(helpers, chain):
    contract = helpers.make_contract()
    contract = contract.model_copy(update={"requirements": [r for r in contract.requirements if r.claim != "asset_price"]})
    contract = contract.model_copy(update={"policy": contract.policy.model_copy(update={"minimum_required_evidence": 2})})
    items = [i for i in helpers.make_evidence(contract, NOW) if i.claim.name != "asset_price"]
    decision = evaluate(contract, EvidenceSet(contract_id=contract.contract_id, items=items), NOW)
    assert decision.state == DecisionState.AUTHORIZE, decision.summary
    action = prepare_action("demo_swap", contract=contract, decision=decision, evidence_items=items, chain=chain, settings=S())
    assert action.min_amount_out == 1
    assert "no fresh asset_price evidence" in action.description
    assert action.recipient == GATE  # default recipient: the gate itself (protected treasury)
    assert verify_spend(action, chain) == []


def test_demo_swap_ignores_stale_price_items(helpers, chain):
    contract, items, decision = decide(helpers)
    stale = helpers.make_item(contract, "r_price", "asset_price", Decimal("0.1"), NOW - timedelta(seconds=3600), evidence_id="ev_old_price", provider="old")
    decision = evaluate(contract, EvidenceSet(contract_id=contract.contract_id, items=items + [stale]), NOW)
    assert decision.state == DecisionState.AUTHORIZE
    action = prepare_action("demo_swap", contract=contract, decision=decision, evidence_items=items + [stale], chain=chain, settings=S(helpers.RECIPIENT))
    # 0.1 would give minOut ~990 TOKEN-X: the stale observation must not be used.
    assert action.min_amount_out == 231_308_411_214_953_271_028


def test_demo_swap_refuses_what_it_cannot_represent(helpers, chain):
    contract, items, decision = decide(helpers, action_type=ActionType.SELL)
    with pytest.raises(AdapterError) as exc:
        prepare_action("demo_swap", contract=contract, decision=decision, evidence_items=items, chain=chain, settings=S())
    assert exc.value.code == "UNSUPPORTED_ACTION"
    contract, items, decision = decide(helpers, asset="WETH")
    with pytest.raises(AdapterError) as exc:
        prepare_action("demo_swap", contract=contract, decision=decision, evidence_items=items, chain=chain, settings=S())
    assert exc.value.code == "UNSUPPORTED_ASSET"
    contract, items, decision = decide(helpers)
    no_router = ChainInfo(**{**chain.__dict__, "router_address": None})
    with pytest.raises(AdapterError) as exc:
        prepare_action("demo_swap", contract=contract, decision=decision, evidence_items=items, chain=no_router, settings=S())
    assert exc.value.code == "CHAIN_NOT_CONFIGURED"
    with pytest.raises(AdapterError):
        prepare_action("demo_swap", contract=contract, decision=decision, evidence_items=items, chain=chain, settings=S(slippage_bps=10_000))
    with pytest.raises(AdapterError) as exc:
        get_adapter("uniswap")
    assert exc.value.code == "UNKNOWN_ADAPTER"
    assert isinstance(get_adapter("demo-swap"), DemoSwapAdapter) and isinstance(get_adapter("transfer"), TransferAdapter)


def test_deny_decision_has_no_action_and_no_authorization(helpers, chain):
    contract, items, decision = decide(helpers, liquidity=Decimal("92440"))
    assert decision.state == DecisionState.DENY
    with pytest.raises(AdapterError) as exc:
        prepare_action("demo_swap", contract=contract, decision=decision, evidence_items=items, chain=chain, settings=S())
    assert exc.value.code == "NO_AUTHORITY_WINDOW"
    # Even an action prepared by hand cannot become an authorization.
    _, _, ok_decision, action = swap(helpers, chain)
    with pytest.raises(AuthorityError) as exc:
        build_authorization(decision=decision, contract=contract, action=action, executor=EXECUTOR, onchain_intent_id=INTENT_ID)
    assert exc.value.code == ReasonCode.EVIDENCE_AUTHORIZATION_REQUIRED


# ---------------------------------------------------------------------------------------------
# TransferAdapter
# ---------------------------------------------------------------------------------------------


def test_transfer_adapter(helpers, chain):
    contract, items, decision = decide(helpers)
    action = prepare_action("transfer", contract=contract, decision=decision, evidence_items=items, chain=chain, settings=S(helpers.RECIPIENT))
    amount = 100 * helpers.USDC
    assert action.target == USDC_ADDR and action.asset_in == ZERO_ADDRESS and action.max_amount_in == 0
    assert action.asset_out == USDC_ADDR and action.min_amount_out == amount == action.spend_raw
    assert action.data == "0x" + (keccak(text=TRANSFER_SIGNATURE)[:4] + abi_encode(["address", "uint256"], [helpers.RECIPIENT, amount])).hex()
    assert verify_spend(action, chain) == []
    build_authorization(decision=decision, contract=contract, action=action, executor=EXECUTOR, onchain_intent_id=INTENT_ID)
    for bad in (S(None), S(GATE)):
        with pytest.raises(AdapterError) as exc:
            prepare_action("transfer", contract=contract, decision=decision, evidence_items=items, chain=chain, settings=bad)
        assert exc.value.code == "RECIPIENT_REQUIRED"


# ---------------------------------------------------------------------------------------------
# verify_spend: tampered actions
# ---------------------------------------------------------------------------------------------


def _swap_data(helpers, amount_in, min_out, recipient, token_in=USDC_ADDR, token_out=TOKENX_ADDR):
    return "0x" + (
        keccak(text=SWAP_EXACT_IN_SIGNATURE)[:4]
        + abi_encode(["address", "address", "uint256", "uint256", "address"], [token_in, token_out, amount_in, min_out, recipient])
    ).hex()


def test_verify_spend_detects_tampered_swap_calldata(helpers, chain):
    *_, action = swap(helpers, chain)
    attacker = "0x000000000000000000000000000000000000dEaD"
    big = 10_000 * helpers.USDC
    cases = {
        # calldata pulls more than the declared spend / approval
        "amount": (action.model_copy(update={"data": _swap_data(helpers, big, action.min_amount_out, action.recipient)}), {"max_amount_in", "spend_raw"}),
        # calldata sends the output elsewhere
        "recipient": (action.model_copy(update={"data": _swap_data(helpers, action.max_amount_in, action.min_amount_out, attacker)}), {"recipient"}),
        # calldata's router floor differs from the declared output floor
        "min_out": (action.model_copy(update={"data": _swap_data(helpers, action.max_amount_in, 1, action.recipient)}), {"min_amount_out"}),
        # calldata swaps another pair
        "pair": (action.model_copy(update={"data": _swap_data(helpers, action.max_amount_in, action.min_amount_out, action.recipient, token_in=TOKENX_ADDR, token_out=USDC_ADDR)}), {"calldata_token_in", "calldata_token_out"}),
        # declared fields tampered, calldata untouched
        "declared_spend": (action.model_copy(update={"spend_raw": 1}), {"spend_raw"}),
        "declared_recipient": (action.model_copy(update={"recipient": attacker}), {"recipient"}),
        "target": (action.model_copy(update={"target": attacker}), {"target"}),
        "asset_in": (action.model_copy(update={"asset_in": TOKENX_ADDR}), {"asset_in"}),
        "value": (action.model_copy(update={"value": 1}), {"value"}),
        "gate": (action.model_copy(update={"gate_address": attacker}), {"gate_address"}),
        "chain": (action.model_copy(update={"chain_id": 143}), {"chain_id"}),
        "decimals": (action.model_copy(update={"amount_decimals": 18}), {"amount_decimals"}),
        "garbage": (action.model_copy(update={"data": "0x1234"}), {"calldata"}),
        "trailing_bytes": (action.model_copy(update={"data": action.data + "00"}), {"calldata"}),
        "other_function": (action.model_copy(update={"data": "0xa9059cbb" + action.data[10:]}), {"calldata"}),
    }
    for label, (tampered, expected) in cases.items():
        problems = set(verify_spend(tampered, chain))
        assert expected <= problems, (label, problems)
        assert problems, label


def test_verify_spend_detects_tampered_transfer(helpers, chain):
    contract, items, decision = decide(helpers)
    action = prepare_action("transfer", contract=contract, decision=decision, evidence_items=items, chain=chain, settings=S(helpers.RECIPIENT))
    data = lambda to, amount: "0x" + (keccak(text=TRANSFER_SIGNATURE)[:4] + abi_encode(["address", "uint256"], [to, amount])).hex()  # noqa: E731
    attacker = "0x000000000000000000000000000000000000dEaD"
    assert "spend_raw" in verify_spend(action.model_copy(update={"data": data(helpers.RECIPIENT, 10**12)}), chain)
    assert "recipient" in verify_spend(action.model_copy(update={"data": data(attacker, action.spend_raw)}), chain)
    assert "min_amount_out" in verify_spend(action.model_copy(update={"min_amount_out": 1}), chain)
    assert "asset_in" in verify_spend(action.model_copy(update={"asset_in": USDC_ADDR, "max_amount_in": 5}), chain)
    assert "target" in verify_spend(action.model_copy(update={"target": TOKENX_ADDR}), chain)
    assert "spend_raw" in verify_spend(action.model_copy(update={"spend_raw": None}), chain)


def test_verify_spend_unknown_adapter(helpers, chain):
    *_, action = swap(helpers, chain)
    assert verify_spend(action.model_copy(update={"adapter": "uniswap_v3"}), chain) == ["unknown_adapter"]
    assert verify_spend(action.model_copy(update={"adapter": ""}), chain) == ["unknown_adapter"]


def test_build_authorization_bounds_raw_spend(helpers, chain):
    """Shared invariant (build_authorization): the raw approval can never exceed the contract maximum."""
    contract, _, decision, action = swap(helpers, chain)
    too_much = action.model_copy(update={"max_amount_in": 100 * helpers.USDC + 1})
    with pytest.raises(AuthorityError) as exc:
        build_authorization(decision=decision, contract=contract, action=too_much, executor=EXECUTOR, onchain_intent_id=INTENT_ID)
    assert exc.value.code == ReasonCode.AUTHORITY_EXPANSION
