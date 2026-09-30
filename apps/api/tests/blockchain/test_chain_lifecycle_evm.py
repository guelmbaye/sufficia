"""Full authorize -> execute -> receipt lifecycle on LocalEvmChain through the REAL backend path:
Decision Engine -> execution adapter -> build_authorization -> Web3Gateway (locally signed raw txs).

Every negative case asserts the fail-closed shape: a revert caught in simulation is REVERTED with
``tx_hash=None`` (nothing sent), and the chain state (balances, nonces, authorization) is unchanged.
"""

from __future__ import annotations

import secrets
import threading
import time
from decimal import Decimal

import pytest
from eth_utils import keccak

from sufficia.canonical.hashes import canonical_action_hash, receipt_hash
from sufficia.decision_engine import AuthorityError, build_authorization
from sufficia.domain.enums import DecisionState, TxPurpose, TxStatus
from sufficia.domain.reasons import ReasonCode
from sufficia.execution import AdapterError, prepare_action, verify_spend

pytestmark = pytest.mark.evm


def snapshot(chain, *holders_extra: str) -> dict:
    g = chain.gateway
    info = g.info
    holders = {"gate": info.gate_address, "router": info.router_address, "recipient": "0x000000000000000000000000000000000000bEEF", **{h: h for h in holders_extra}}
    out = {}
    for name, holder in holders.items():
        out[(name, "usdc")] = g.token_balance(info.usdc_address, holder)
        out[(name, "tokenx")] = g.token_balance(info.token_x_address, holder)
    for role in ("authorizer", "executor"):
        out[(role, "nonce")] = chain.w3.eth.get_transaction_count(chain.address(role))
    return out


def assert_not_sent(result, error_name: str) -> None:
    assert result.status == TxStatus.REVERTED, (result.status, result.error)
    assert result.tx_hash is None, "a failing action must not be broadcast"
    assert result.error_name == error_name, (result.error_name, result.error)
    assert not result.ok and result.events == []


# ---------------------------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------------------------


def test_demo_swap_full_lifecycle(local_chain, flow_factory, helpers):
    g = local_chain.gateway
    flow = flow_factory(local_chain)

    decision = flow.decide()
    assert decision.state == DecisionState.AUTHORIZE, decision.summary
    action = flow.prepare()
    assert verify_spend(action, g.info) == []
    auth = flow.authorization()

    # Parity: the backend's action hash == EvidenceGate.computeActionHash(action).
    assert g.compute_action_hash(action) == canonical_action_hash(action) == auth.action_hash

    # 1. registerIntent (+ idempotent replay: no transaction).
    registered = g.register_intent(flow.onchain_intent_id, flow.contract_hash)
    assert registered.status == TxStatus.CONFIRMED and registered.tx_hash and registered.purpose == TxPurpose.INTENT_REGISTER
    assert registered.events == [
        {
            "event": "IntentRegistered",
            "args": {"intentId": flow.onchain_intent_id, "contractHash": flow.contract_hash, "registeredAt": registered.block_timestamp},
            "log_index": 0,
        }
    ]
    assert g.intent_contract(flow.onchain_intent_id) == flow.contract_hash
    again = g.register_intent(flow.onchain_intent_id, flow.contract_hash)
    assert again.status == TxStatus.CONFIRMED and again.tx_hash is None and again.events == []

    # 2. authorize.
    authorized = g.authorize(auth)
    assert authorized.ok, authorized.error
    (event,) = authorized.events
    assert event["event"] == "ExecutionAuthorized"
    assert event["args"] == {
        "authorizationId": auth.authorization_id,
        "intentId": flow.onchain_intent_id,
        "evidenceRoot": decision.evidence_root,
        "contractHash": decision.contract_hash,
        "actionHash": auth.action_hash,
        "executor": g.info.executor,
        "expiresAt": int(auth.expires_at.timestamp()),
    }
    onchain = g.get_authorization(auth.authorization_id)
    assert onchain.exists and onchain.active and not onchain.consumed
    assert (onchain.action_hash, onchain.executor, onchain.evidence_root) == (auth.action_hash, g.info.executor, auth.evidence_root)
    assert onchain.issued_at == int(auth.issued_at.timestamp()) and onchain.expires_at == int(auth.expires_at.timestamp())

    # 3. execute.
    before = snapshot(local_chain)
    executed = g.execute(auth.authorization_id, action)
    assert executed.ok, (executed.error_name, executed.error)
    assert executed.purpose == TxPurpose.PROTECTED_EXECUTION and executed.from_address == g.info.executor
    assert executed.gas_used and executed.block_number and executed.explorer_url is None  # local: no explorer
    names = [e["event"] for e in executed.events]
    assert names == ["AuthorizationConsumed", "ProtectedExecution"]
    consumed, protected = executed.events
    assert consumed["args"] == {"authorizationId": auth.authorization_id, "actionHash": auth.action_hash, "executor": g.info.executor}
    assert protected["args"]["target"] == g.info.router_address and protected["args"]["actionHash"] == auth.action_hash
    assert protected["args"]["resultHash"].startswith("0x") and len(protected["args"]["resultHash"]) == 66
    assert consumed["log_index"] < protected["log_index"]

    after = snapshot(local_chain)
    assert after[("router", "usdc")] - before[("router", "usdc")] == 100 * helpers.USDC
    assert before[("gate", "usdc")] - after[("gate", "usdc")] == 100 * helpers.USDC
    received = after[("recipient", "tokenx")] - before[("recipient", "tokenx")]
    assert received == 233_600_000_000_000_000_000 >= action.min_amount_out
    # Bounded spend: the approval was reset to 0.
    usdc = local_chain.w3.eth.contract(address=g.info.usdc_address, abi=g._erc20_abi)
    assert usdc.functions.allowance(g.info.gate_address, g.info.router_address).call() == 0

    onchain = g.get_authorization(auth.authorization_id)
    assert onchain.consumed and not onchain.active

    # 4. receipt commitment.
    r_hash = receipt_hash({"authorization_id": auth.authorization_id, "tx": executed.tx_hash})
    assert g.receipt_commitment(auth.authorization_id) is None
    committed = g.record_receipt(auth.authorization_id, r_hash)
    assert committed.ok and committed.purpose == TxPurpose.RECEIPT_COMMITMENT
    assert committed.events == [{"event": "ReceiptCommitted", "args": {"authorizationId": auth.authorization_id, "receiptHash": r_hash}, "log_index": 0}]
    assert g.receipt_commitment(auth.authorization_id) == r_hash
    assert_not_sent(g.record_receipt(auth.authorization_id, r_hash), "ReceiptAlreadyRecorded")

    # Reconciliation lookups rebuild the same result.
    found = g.find_transaction(executed.tx_hash)
    assert found.status == TxStatus.CONFIRMED and found.purpose == TxPurpose.PROTECTED_EXECUTION
    assert found.events == executed.events and found.block_number == executed.block_number
    assert g.find_transaction("0x" + "ab" * 32) is None


def test_transfer_adapter_lifecycle(local_chain, flow_factory, helpers):
    g = local_chain.gateway
    flow = flow_factory(local_chain, adapter="transfer")
    auth = flow.authorize_onchain()
    assert flow.action.target == g.info.usdc_address and verify_spend(flow.action, g.info) == []
    before = snapshot(local_chain)
    executed = g.execute(auth.authorization_id, flow.action)
    assert executed.ok, executed.error
    after = snapshot(local_chain)
    assert after[("recipient", "usdc")] - before[("recipient", "usdc")] == 100 * helpers.USDC
    assert before[("gate", "usdc")] - after[("gate", "usdc")] == 100 * helpers.USDC
    assert [e["event"] for e in executed.events] == ["AuthorizationConsumed", "ProtectedExecution"]
    assert executed.events[1]["args"]["target"] == g.info.usdc_address


# ---------------------------------------------------------------------------------------------
# Negative cases (fail closed)
# ---------------------------------------------------------------------------------------------


def test_execute_without_authorization(local_chain, flow_factory):
    g = local_chain.gateway
    flow = flow_factory(local_chain)
    flow.decide()
    action = flow.prepare()
    missing = "0x" + secrets.token_hex(32)
    before = snapshot(local_chain)
    assert_not_sent(g.execute(missing, action), "AuthorizationNotFound")
    assert snapshot(local_chain) == before  # no balance moved, no nonce consumed
    assert g.get_authorization(missing).exists is False


def test_mined_revert_is_reverted_with_decoded_error(local_chain, flow_factory):
    """Explicit gas skips simulation: the tx is mined with status 0, decoded by replaying eth_call."""
    g = local_chain.gateway
    flow = flow_factory(local_chain)
    flow.decide()
    action = flow.prepare()
    from sufficia.blockchain.web3_gateway import action_tuple
    from sufficia.canonical.hashes import unhex32

    missing = "0x" + secrets.token_hex(32)
    before = snapshot(local_chain)
    result = g.send_gate_call(TxPurpose.PROTECTED_EXECUTION, "executor", "executeAuthorized", [unhex32(missing), action_tuple(action)], gas=500_000)
    assert result.status == TxStatus.REVERTED and result.tx_hash and result.block_number
    assert result.error_name == "AuthorizationNotFound" and result.events == []
    after = snapshot(local_chain)
    assert after[("executor", "nonce")] == before[("executor", "nonce")] + 1  # gas spent, nothing else
    assert {k: v for k, v in after.items() if k[1] != "nonce"} == {k: v for k, v in before.items() if k[1] != "nonce"}
    found = g.find_transaction(result.tx_hash)
    assert found.status == TxStatus.REVERTED and found.error_name == "AuthorizationNotFound"
    assert found.purpose == TxPurpose.PROTECTED_EXECUTION


def test_changed_action_is_rejected(local_chain, flow_factory, helpers):
    g = local_chain.gateway
    flow = flow_factory(local_chain)
    auth = flow.authorize_onchain()
    action = flow.action
    attacker = "0x000000000000000000000000000000000000dEaD"
    tampered = [
        action.model_copy(update={"max_amount_in": action.max_amount_in + 1}),  # amount changed
        action.model_copy(update={"min_amount_out": 1}),
        action.model_copy(update={"recipient": attacker}),
        action.model_copy(update={"deadline": action.deadline - 1}),
        action.model_copy(update={"nonce": "0x" + secrets.token_hex(32)}),
    ]
    before = snapshot(local_chain)
    for bad in tampered:
        assert canonical_action_hash(bad) != auth.action_hash
        assert_not_sent(g.execute(auth.authorization_id, bad), "ActionHashMismatch")
    assert snapshot(local_chain) == before
    # The authorization survives the attempts and the exact action still executes once.
    assert g.get_authorization(auth.authorization_id).active
    assert g.execute(auth.authorization_id, action).ok


def test_reuse_is_rejected(local_chain, flow_factory):
    g = local_chain.gateway
    flow = flow_factory(local_chain)
    auth = flow.authorize_onchain()
    assert g.execute(auth.authorization_id, flow.action).ok
    before = snapshot(local_chain)
    assert_not_sent(g.execute(auth.authorization_id, flow.action), "AuthorizationAlreadyConsumed")
    assert snapshot(local_chain) == before


def test_wrong_executor_key_is_rejected(local_chain, flow_factory, helpers):
    g = local_chain.gateway
    flow = flow_factory(local_chain)
    auth = flow.authorize_onchain()
    before = snapshot(local_chain)
    for role in ("authorizer", "pauser", "deployer"):  # funded keys that are not the named executor
        result = g.execute(auth.authorization_id, flow.action, executor_key=helpers.LOCAL_DEV_KEYS[role])
        assert_not_sent(result, "UnauthorizedExecutor")
    # An unfunded key cannot even be simulated by eth-tester (fees are validated in eth_call): still
    # FAILED and never broadcast.
    unfunded = g.execute(auth.authorization_id, flow.action, executor_key="0x" + secrets.token_hex(32))
    assert unfunded.status == TxStatus.FAILED and unfunded.tx_hash is None and not unfunded.ok
    assert snapshot(local_chain) == before
    assert g.get_authorization(auth.authorization_id).active
    assert g.execute(auth.authorization_id, flow.action).ok


def test_authorization_replay_and_bad_authorizations(local_chain, flow_factory, helpers):
    g = local_chain.gateway
    flow = flow_factory(local_chain)
    auth = flow.authorize_onchain()
    assert_not_sent(g.authorize(auth), "AuthorizationExists")
    # Unregistered intent.
    other = flow_factory(local_chain)
    other.decide()
    other.prepare()
    unregistered = other.authorization()
    assert_not_sent(g.authorize(unregistered), "IntentNotRegistered")
    # Tampered binding: the id no longer matches its fields.
    g.register_intent(other.onchain_intent_id, other.contract_hash)
    forged = unregistered.model_copy(update={"evidence_root": "0x" + secrets.token_hex(32)})
    assert_not_sent(g.authorize(forged), "AuthorizationIdMismatch")
    # Intent registered with another contract hash.
    assert_not_sent(g.register_intent(other.onchain_intent_id, "0x" + secrets.token_hex(32)), "IntentAlreadyRegistered")
    # Receipt before execution.
    assert g.authorize(unregistered).ok
    assert_not_sent(g.record_receipt(unregistered.authorization_id, "0x" + "11" * 32), "AuthorizationNotConsumed")


def test_deny_decision_cannot_reach_the_chain(local_chain, flow_factory):
    g = local_chain.gateway
    flow = flow_factory(local_chain)
    decision = flow.decide(liquidity=Decimal("92440"))  # below the $500K requirement
    assert decision.state == DecisionState.DENY and decision.authority_window is None
    with pytest.raises(AdapterError):
        prepare_action("demo_swap", contract=flow.contract, decision=decision, evidence_items=flow.evidence, chain=g.info, settings=flow.settings)
    # Even with an action borrowed from an AUTHORIZE decision, no authorization can be built.
    ok = flow_factory(local_chain)
    ok.decide()
    borrowed = ok.prepare()
    with pytest.raises(AuthorityError) as exc:
        build_authorization(decision=decision, contract=flow.contract, action=borrowed, executor=g.info.executor, onchain_intent_id=flow.onchain_intent_id)
    assert exc.value.code == ReasonCode.EVIDENCE_AUTHORIZATION_REQUIRED


def test_price_mismatch_surfaces_nested_router_error(local_chain, flow_factory):
    """Price evidence (0.30) disagrees with the venue (0.428): the router's own floor fails inside the gate."""
    g = local_chain.gateway
    flow = flow_factory(local_chain)
    auth = flow.authorize_onchain(prices=(Decimal("0.30"),))
    assert flow.action.min_amount_out > 233_600_000_000_000_000_000
    before = snapshot(local_chain)
    result = g.execute(auth.authorization_id, flow.action)
    assert_not_sent(result, "ExecutionFailed(InsufficientOutput)")
    assert "InsufficientOutput(amountOut=233600000000000000000" in result.error
    assert snapshot(local_chain) == before
    # Atomic: the authorization was not consumed by the failed attempt.
    assert g.get_authorization(auth.authorization_id).active


def test_gate_output_floor_slippage_exceeded(local_chain, flow_factory):
    """A declared output floor above what the venue delivers (router floor untouched) -> SlippageExceeded.
    verify_spend flags the inconsistency before anything is authorized."""
    g = local_chain.gateway
    flow = flow_factory(local_chain)
    flow.decide()
    flow.action = flow.prepare().model_copy(update={"min_amount_out": 300 * 10**18})
    assert "min_amount_out" in verify_spend(flow.action, g.info)
    auth = flow.authorization()
    assert g.register_intent(flow.onchain_intent_id, flow.contract_hash).ok
    assert g.authorize(auth).ok
    result = g.execute(auth.authorization_id, flow.action)
    assert_not_sent(result, "SlippageExceeded")
    assert "received=233600000000000000000" in result.error and "minAmountOut=300000000000000000000" in result.error


# ---------------------------------------------------------------------------------------------
# Time, pause, concurrency, timeouts (dedicated chains: these mutate global chain state)
# ---------------------------------------------------------------------------------------------


def test_expired_authorization(fresh_chain, flow_factory):
    g = fresh_chain.gateway
    flow = flow_factory(fresh_chain)
    auth = flow.authorize_onchain()
    remaining = int(auth.expires_at.timestamp()) - g.block_timestamp()
    assert 0 < remaining <= 60
    fresh_chain.advance_time(remaining + 1)
    assert g.block_timestamp() > int(auth.expires_at.timestamp())
    onchain = g.get_authorization(auth.authorization_id)
    assert onchain.exists and not onchain.active and not onchain.consumed
    before = snapshot(fresh_chain)
    assert_not_sent(g.execute(auth.authorization_id, flow.action), "AuthorizationExpired")
    assert snapshot(fresh_chain) == before


def test_authorization_committed_after_expiry_is_rejected(fresh_chain, flow_factory):
    g = fresh_chain.gateway
    flow = flow_factory(fresh_chain)
    flow.decide()
    flow.prepare()
    auth = flow.authorization()
    assert g.register_intent(flow.onchain_intent_id, flow.contract_hash).ok
    fresh_chain.advance_time(120)
    assert_not_sent(g.authorize(auth), "AuthorizationExpired")


def test_paused_gate(fresh_chain, flow_factory):
    g = fresh_chain.gateway
    flow = flow_factory(fresh_chain)
    auth = flow.authorize_onchain()
    assert g.pause().ok and g.is_paused()
    assert_not_sent(g.execute(auth.authorization_id, flow.action), "EnforcedPause")
    second = flow_factory(fresh_chain)
    second.decide()
    second.prepare()
    second_auth = second.authorization()
    assert g.register_intent(second.onchain_intent_id, second.contract_hash).ok  # not pausable
    assert_not_sent(g.authorize(second_auth), "EnforcedPause")
    assert g.unpause().ok and not g.is_paused()
    assert g.execute(auth.authorization_id, flow.action).ok
    # Receipts can still be committed while paused.
    assert g.pause().ok
    assert g.record_receipt(auth.authorization_id, "0x" + "42" * 32).ok
    # Only the pauser can pause.
    assert g.send_gate_call(TxPurpose.DEPLOYMENT, "executor", "unpause", []).error_name == "AccessControlUnauthorizedAccount"


def test_chain_time_tracks_wall_clock_under_bursts(fresh_chain):
    g = fresh_chain.gateway
    for _ in range(25):
        assert g.register_intent("0x" + secrets.token_hex(32), "0x" + secrets.token_hex(32)).ok
    now = int(time.time())
    assert now <= g.block_timestamp() <= now + 1
    assert fresh_chain.latest_timestamp() <= now + 1
    target = fresh_chain.advance_time(300)
    assert target >= now + 300 and g.block_timestamp() == target
    result = g.register_intent("0x" + secrets.token_hex(32), "0x" + secrets.token_hex(32))
    assert result.block_timestamp == target
    assert g.block_timestamp() >= target  # never backwards


def test_concurrent_transactions_from_threads(fresh_chain, flow_factory):
    """FastAPI runs sync endpoints in a thread pool: one queue per signer, no nonce collisions."""
    g = fresh_chain.gateway
    flows = [flow_factory(fresh_chain) for _ in range(3)]
    for flow in flows:
        flow.authorize_onchain()
    results: list = []
    errors: list = []

    def register() -> None:
        try:
            results.append(g.register_intent("0x" + secrets.token_hex(32), "0x" + secrets.token_hex(32)))
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    def execute(flow) -> None:
        try:
            results.append(g.execute(flow.auth.authorization_id, flow.action))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=register) for _ in range(8)] + [threading.Thread(target=execute, args=(f,)) for f in flows]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors
    assert len(results) == 11 and all(r.ok for r in results), [(r.error_name, r.error) for r in results if not r.ok]
    assert len({r.tx_hash for r in results}) == 11
    for role, count in (("authorizer", 8 + 3 * 2), ("executor", 3)):
        assert fresh_chain.w3.eth.get_transaction_count(fresh_chain.address(role)) == count


def test_receipt_timeout_returns_submitted_then_reconciles(fresh_chain):
    g = fresh_chain.gateway
    fresh_chain.tester.disable_auto_mine_transactions()
    original_timeout = g.sender.tx_timeout
    g.sender.tx_timeout = 0.2
    try:
        intent, c_hash = "0x" + secrets.token_hex(32), "0x" + secrets.token_hex(32)
        result = g.register_intent(intent, c_hash)
        assert result.status == TxStatus.SUBMITTED and result.tx_hash and not result.ok
        assert "reconcile" in result.error
        pending = g.find_transaction(result.tx_hash)
        assert pending.status == TxStatus.SUBMITTED and pending.purpose == TxPurpose.INTENT_REGISTER
        fresh_chain.mine()
        found = g.find_transaction(result.tx_hash)
        assert found.status == TxStatus.CONFIRMED and [e["event"] for e in found.events] == ["IntentRegistered"]
        assert g.intent_contract(intent) == c_hash
    finally:
        g.sender.tx_timeout = original_timeout
        fresh_chain.tester.enable_auto_mine_transactions()


def test_health_and_info(local_chain):
    g = local_chain.gateway
    health = g.health()
    assert health["ok"] and health["gate_deployed"] and health["chain_id_matches"] and health["paused"] is False
    assert health["network"] == "local-evm (py-evm, not Monad)"
    assert health["signers"]["authorizer"]["has_role"] and health["signers"]["pauser"]["has_role"]
    assert health["signers"]["executor"]["balance_wei"] > 0
    public = g.info.public()
    assert public["mode"] == "LOCAL_EVM" and public["explorer_url"] is None
    assert public["router"] == local_chain.deployment.router
    assert g.has_role("authorizer", g.info.authorizer) and not g.has_role("authorizer", g.info.executor)
    assert g.explorer_tx_url("0x" + "ab" * 32) is None
    # Selector table used by find_transaction.
    assert keccak(text="executeAuthorized(bytes32,(address,uint256,bytes,address,uint256,address,uint256,address,uint64,bytes32))")[:4] in g._selectors
