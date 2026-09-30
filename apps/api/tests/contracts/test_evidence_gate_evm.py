"""Executed tests for the SUFFICIA contract layer (EvidenceGate + demo token/router) on py-evm.

Run: cd apps/api && python -m pytest tests/contracts -q
Requires compiled artifacts: cd contracts && npm install && npm run build

All action hashes and authorization ids are computed independently in Python (eth_abi + keccak) and
must match the contract byte for byte; that is the contract between the backend and the chain.
"""

from __future__ import annotations

import random
import secrets

import pytest
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address

# ---------------------------------------------------------------------------
# Signatures the backend depends on (pinned: changing them is a breaking change)
# ---------------------------------------------------------------------------

EXPECTED_EVENTS = {
    "IntentRegistered(bytes32,bytes32,uint256)",
    "ExecutionAuthorized(bytes32,bytes32,bytes32,bytes32,bytes32,address,uint64)",
    "AuthorizationConsumed(bytes32,bytes32,address)",
    "ProtectedExecution(bytes32,address,bytes32,bytes32)",
    "ReceiptCommitted(bytes32,bytes32)",
    "TargetAllowed(address,bool)",
    "Swept(address,address,uint256)",
}

EXPECTED_GATE_ERRORS = {
    "InvalidAddress()",
    "InvalidTarget()",
    "InvalidIntent()",
    "IntentAlreadyRegistered()",
    "IntentNotRegistered()",
    "ContractHashMismatch()",
    "InvalidAuthorization()",
    "AuthorizationIdMismatch()",
    "AuthorizationExists()",
    "AuthorizationExpired()",
    "TtlTooLong()",
    "AuthorizationNotFound()",
    "AuthorizationAlreadyConsumed()",
    "AuthorizationNotConsumed()",
    "UnauthorizedExecutor()",
    "ActionHashMismatch()",
    "TargetNotAllowed()",
    "ActionDeadlinePassed()",
    "ValueMismatch()",
    "ExecutionFailed(bytes)",
    "SlippageExceeded(uint256,uint256)",
    "ReceiptAlreadyRecorded()",
    "InvalidReceipt()",
}

ACCESS_DENIED = "AccessControlUnauthorizedAccount(address,bytes32)"
ENFORCED_PAUSE = "EnforcedPause()"


def _sig(item: dict) -> str:
    return f"{item['name']}({','.join(i['type'] for i in item['inputs'])})"


def _is_active(env, authorization_id: bytes) -> bool:
    # "pending" = the block the next transaction will execute in.
    return env.gate.functions.isAuthorizationActive(authorization_id).call(block_identifier="pending")


def _execute(env, auth: dict, action, sender=None):
    return env.send(env.execute_fn(auth["authorizationId"], action), sender or env.executor, value=action.value)


# ===========================================================================
# Deployment, constants, ABI surface
# ===========================================================================


def test_deployment_roles_targets_and_funding(gate_env, evm):
    env = gate_env
    g = env.gate.functions
    assert g.hasRole(evm.DEFAULT_ADMIN_ROLE, env.admin).call()
    assert g.hasRole(evm.AUTHORIZER_ROLE, env.authorizer).call()
    assert g.hasRole(evm.PAUSER_ROLE, env.pauser).call()
    assert not g.hasRole(evm.AUTHORIZER_ROLE, env.executor).call()
    assert not g.hasRole(evm.AUTHORIZER_ROLE, env.admin).call()
    assert g.allowedTargets(env.router.address).call()
    assert not g.allowedTargets(env.attacker).call()
    assert not g.paused().call()
    assert env.balance(env.usdc, env.gate.address) == evm.GATE_USDC_FUNDING
    assert env.balance(env.tokenx, env.router.address) == evm.ROUTER_TOKENX_RESERVE
    assert env.usdc.functions.decimals().call() == 6
    assert env.tokenx.functions.decimals().call() == 18


def test_domain_constants_match_python(gate_env, evm):
    g = gate_env.gate.functions
    assert g.ACTION_DOMAIN().call() == evm.ACTION_DOMAIN == keccak(text="EVIDENCE_GATE_ACTION_V1")
    assert g.AUTH_DOMAIN().call() == evm.AUTH_DOMAIN == keccak(text="EVIDENCE_GATE_AUTH_V1")
    assert g.AUTHORIZER_ROLE().call() == keccak(text="AUTHORIZER_ROLE")
    assert g.PAUSER_ROLE().call() == keccak(text="PAUSER_ROLE")
    assert g.MAX_AUTHORIZATION_TTL().call() == 600


def test_abi_exposes_pinned_events_and_errors(gate_env):
    abi = gate_env.gate.abi
    events = {_sig(i) for i in abi if i["type"] == "event"}
    errors = {_sig(i) for i in abi if i["type"] == "error"}
    assert EXPECTED_EVENTS <= events, EXPECTED_EVENTS - events
    assert EXPECTED_GATE_ERRORS <= errors, EXPECTED_GATE_ERRORS - errors


def test_constructor_rejects_zero_role_addresses_and_bad_targets(gate_env, evm):
    env = gate_env
    z = evm.ZERO_ADDRESS
    env.expect_deploy_revert("EvidenceGate", "InvalidAddress()", z, env.authorizer, env.pauser, [])
    env.expect_deploy_revert("EvidenceGate", "InvalidAddress()", env.admin, z, env.pauser, [])
    env.expect_deploy_revert("EvidenceGate", "InvalidAddress()", env.admin, env.authorizer, z, [])
    env.expect_deploy_revert("EvidenceGate", "InvalidTarget()", env.admin, env.authorizer, env.pauser, [z])


# ===========================================================================
# Hash encodings (backend parity)
# ===========================================================================


def test_action_hash_matches_independent_python_encoding(gate_env):
    env = gate_env
    action = env.swap_action()
    onchain = env.gate.functions.computeActionHash(action.as_tuple()).call()
    assert onchain == env.action_hash(action)
    # Explicit re-derivation, spelled out, so the encoding is documented by the test itself.
    manual = keccak(
        abi_encode(
            ["bytes32", "uint256", "address", "address", "uint256", "bytes32", "address", "uint256",
             "address", "uint256", "address", "uint64", "bytes32"],
            [keccak(text="EVIDENCE_GATE_ACTION_V1"), env.chain_id, env.gate.address, action.target,
             action.value, keccak(action.data), action.assetIn, action.maxAmountIn, action.assetOut,
             action.minAmountOut, action.recipient, action.deadline, action.nonce],
        )
    )
    assert onchain == manual


def test_authorization_id_matches_independent_python_encoding(gate_env, evm):
    env = gate_env
    contract_hash, evidence_root, action_hash, nonce = (secrets.token_bytes(32) for _ in range(4))
    onchain = env.gate.functions.computeAuthorizationId(
        contract_hash, evidence_root, action_hash, env.executor, nonce
    ).call()
    manual = keccak(
        abi_encode(
            ["bytes32", "bytes32", "bytes32", "bytes32", "address", "bytes32"],
            [keccak(text="EVIDENCE_GATE_AUTH_V1"), contract_hash, evidence_root, action_hash, env.executor, nonce],
        )
    )
    assert onchain == manual == evm.compute_authorization_id(contract_hash, evidence_root, action_hash, env.executor, nonce)


def test_action_hash_is_bound_to_gate_address_and_chain(gate_env, evm):
    env = gate_env
    action = env.swap_action()
    gate2 = env.deploy("EvidenceGate", env.admin, env.authorizer, env.pauser, [env.router.address])
    h1 = env.gate.functions.computeActionHash(action.as_tuple()).call()
    h2 = gate2.functions.computeActionHash(action.as_tuple()).call()
    assert h1 != h2
    assert h2 == evm.compute_action_hash(env.chain_id, gate2.address, action)
    assert evm.compute_action_hash(env.chain_id + 1, env.gate.address, action) != h1

    # An authorization committed on gate #1 does not exist on gate #2 (no cross-deployment replay).
    auth = env.authorize(action)
    env.expect_revert(gate2.functions.executeAuthorized(auth["authorizationId"], action.as_tuple()),
                      env.executor, "AuthorizationNotFound()")


# ===========================================================================
# registerIntent
# ===========================================================================


def test_register_intent_emits_and_is_idempotent(gate_env):
    env = gate_env
    intent_id, contract_hash = keccak(text="intent-1"), keccak(text="contract-1")
    receipt = env.send(env.gate.functions.registerIntent(intent_id, contract_hash), env.authorizer)
    (ev,) = env.events(env.gate, "IntentRegistered", receipt)
    assert ev.args.intentId == intent_id and ev.args.contractHash == contract_hash
    assert ev.args.registeredAt == env.w3.eth.get_block(receipt.blockNumber).timestamp
    assert env.gate.functions.intentContracts(intent_id).call() == contract_hash
    # Same pair again: no-op, no event.
    receipt2 = env.send(env.gate.functions.registerIntent(intent_id, contract_hash), env.authorizer)
    assert env.events(env.gate, "IntentRegistered", receipt2) == []


def test_register_intent_rejects_conflicting_hash_zero_values_and_non_authorizer(gate_env, evm):
    env = gate_env
    intent_id, contract_hash = env.register_intent()
    env.expect_revert(env.gate.functions.registerIntent(intent_id, keccak(text="other")), env.authorizer,
                      "IntentAlreadyRegistered()")
    env.expect_revert(env.gate.functions.registerIntent(evm.ZERO32, contract_hash), env.authorizer, "InvalidIntent()")
    env.expect_revert(env.gate.functions.registerIntent(keccak(text="x"), evm.ZERO32), env.authorizer, "InvalidIntent()")
    env.expect_revert(env.gate.functions.registerIntent(keccak(text="y"), contract_hash), env.attacker,
                      ACCESS_DENIED, (env.attacker, evm.AUTHORIZER_ROLE))
    assert env.gate.functions.intentContracts(intent_id).call() == contract_hash


# ===========================================================================
# authorize
# ===========================================================================


def test_authorize_happy_path(gate_env, evm):
    env = gate_env
    action = env.swap_action()
    auth = env.authorize(action)
    stored = env.get_authorization(auth["authorizationId"])
    assert stored == {
        "intentId": auth["intentId"],
        "contractHash": auth["contractHash"],
        "evidenceRoot": auth["evidenceRoot"],
        "actionHash": env.action_hash(action),
        "executor": auth["executor"],
        "issuedAt": auth["issuedAt"],
        "expiresAt": auth["expiresAt"],
        "consumed": False,
    }
    assert _is_active(env, auth["authorizationId"])

    (ev,) = env.events(env.gate, "ExecutionAuthorized", auth["receipt"])
    assert ev.args.authorizationId == auth["authorizationId"]
    assert ev.args.intentId == auth["intentId"]
    assert ev.args.evidenceRoot == auth["evidenceRoot"]
    assert ev.args.contractHash == auth["contractHash"]
    assert ev.args.actionHash == auth["actionHash"]
    assert ev.args.executor == env.executor
    assert ev.args.expiresAt == auth["expiresAt"]
    # Raw log layout: 3 indexed topics exactly as the backend indexer expects.
    raw = auth["receipt"].logs[-1]
    assert raw.topics[0] == evm.event_topic(
        "ExecutionAuthorized(bytes32,bytes32,bytes32,bytes32,bytes32,address,uint64)")
    assert raw.topics[1:] == [auth["authorizationId"], auth["intentId"], auth["evidenceRoot"]]


def test_authorize_rejects_non_authorizer(gate_env, evm):
    env = gate_env
    auth = env.build_authorization(env.swap_action())
    for who in (env.attacker, env.executor, env.admin, env.pauser):
        env.expect_revert(env.authorize_fn(auth), who, ACCESS_DENIED, (who, evm.AUTHORIZER_ROLE))
    assert not _is_active(env, auth["authorizationId"])


def test_authorize_rejects_duplicate(gate_env):
    env = gate_env
    action = env.swap_action()
    auth = env.authorize(action)
    env.expect_revert(env.authorize_fn(auth), env.authorizer, "AuthorizationExists()")


def test_authorize_rejects_id_mismatch(gate_env):
    env = gate_env
    auth = env.build_authorization(env.swap_action())
    bad = dict(auth, authorizationId=keccak(auth["authorizationId"]))
    env.expect_revert(env.authorize_fn(bad), env.authorizer, "AuthorizationIdMismatch()")
    # Any bound field changed without recomputing the id is also a mismatch.
    for field, value in (("evidenceRoot", keccak(text="forged evidence")),
                         ("actionHash", keccak(text="other action")),
                         ("executor", env.attacker),
                         ("nonce", keccak(text="other nonce"))):
        env.expect_revert(env.authorize_fn(dict(auth, **{field: value})), env.authorizer, "AuthorizationIdMismatch()")


def test_authorize_rejects_unregistered_intent(gate_env):
    env = gate_env
    auth = env.build_authorization(env.swap_action(), register=False,
                                   intent_id=keccak(text="never registered"), contract_hash=keccak(text="c"))
    env.expect_revert(env.authorize_fn(auth), env.authorizer, "IntentNotRegistered()")


def test_authorize_rejects_contract_hash_mismatch(gate_env):
    env = gate_env
    intent_id, _ = env.register_intent()
    auth = env.build_authorization(env.swap_action(), register=False, intent_id=intent_id,
                                   contract_hash=keccak(text="a different contract"))
    env.expect_revert(env.authorize_fn(auth), env.authorizer, "ContractHashMismatch()")


@pytest.mark.parametrize("field", ["evidenceRoot", "actionHash", "executor", "window"])
def test_authorize_rejects_invalid_fields(gate_env, evm, field):
    env = gate_env
    action = env.swap_action()
    intent_id, contract_hash = env.register_intent()
    auth = env.build_authorization(action, register=False, intent_id=intent_id, contract_hash=contract_hash)
    if field == "evidenceRoot":
        auth["evidenceRoot"] = evm.ZERO32
    elif field == "actionHash":
        auth["actionHash"] = evm.ZERO32
    elif field == "executor":
        auth["executor"] = evm.ZERO_ADDRESS
    else:
        auth["expiresAt"] = auth["issuedAt"]  # expiresAt <= issuedAt
    auth["authorizationId"] = evm.compute_authorization_id(
        auth["contractHash"], auth["evidenceRoot"], auth["actionHash"], auth["executor"], auth["nonce"])
    env.expect_revert(env.authorize_fn(auth), env.authorizer, "InvalidAuthorization()")


def test_authorize_rejects_already_expired(gate_env):
    env = gate_env
    auth = env.build_authorization(env.swap_action())
    now = env.pending_timestamp()
    env.expect_revert(env.authorize_fn(dict(auth, issuedAt=now - 700, expiresAt=now - 100)), env.authorizer,
                      "AuthorizationExpired()")
    now = env.pending_timestamp()
    # Boundary: expiresAt == block.timestamp is already expired.
    env.expect_revert(env.authorize_fn(dict(auth, issuedAt=now - 10, expiresAt=now)), env.authorizer,
                      "AuthorizationExpired()")


def test_authorize_rejects_ttl_too_long_and_accepts_max(gate_env, evm):
    env = gate_env
    auth = env.build_authorization(env.swap_action())
    now = env.pending_timestamp()
    env.expect_revert(env.authorize_fn(dict(auth, issuedAt=now, expiresAt=now + evm.MAX_AUTHORIZATION_TTL + 1)),
                      env.authorizer, "TtlTooLong()")
    now = env.pending_timestamp()
    env.send(env.authorize_fn(dict(auth, issuedAt=now, expiresAt=now + evm.MAX_AUTHORIZATION_TTL)), env.authorizer)
    assert env.get_authorization(auth["authorizationId"])["expiresAt"] == now + evm.MAX_AUTHORIZATION_TTL


# ===========================================================================
# executeAuthorized: happy path
# ===========================================================================


def test_execute_valid_swap_through_router(gate_env, evm):
    env = gate_env
    action = env.swap_action()
    auth = env.authorize(action)
    aid = auth["authorizationId"]

    gate_usdc0 = env.balance(env.usdc, env.gate.address)
    router_usdc0 = env.balance(env.usdc, env.router.address)
    rcpt_x0 = env.balance(env.tokenx, env.recipient)
    router_x0 = env.balance(env.tokenx, env.router.address)

    # Return value (pending-block eth_call) = the router's abi-encoded amountOut.
    result = env.execute_fn(aid, action).call({"from": env.executor}, block_identifier="pending")
    assert abi_decode(["uint256"], result) == (evm.SWAP_EXPECTED_OUT,)

    receipt = _execute(env, auth, action)

    assert env.balance(env.usdc, env.gate.address) == gate_usdc0 - evm.SWAP_AMOUNT_IN
    assert env.balance(env.usdc, env.router.address) == router_usdc0 + evm.SWAP_AMOUNT_IN
    assert env.balance(env.tokenx, env.recipient) == rcpt_x0 + evm.SWAP_EXPECTED_OUT  # 233.6 TOKEN-X
    assert env.balance(env.tokenx, env.router.address) == router_x0 - evm.SWAP_EXPECTED_OUT
    # Approval is exact and reset to zero after the call.
    assert env.usdc.functions.allowance(env.gate.address, env.router.address).call() == 0

    assert env.get_authorization(aid)["consumed"] is True
    assert not _is_active(env, aid)

    (consumed,) = env.events(env.gate, "AuthorizationConsumed", receipt)
    assert consumed.args.authorizationId == aid
    assert consumed.args.actionHash == auth["actionHash"]
    assert consumed.args.executor == env.executor
    (protected,) = env.events(env.gate, "ProtectedExecution", receipt)
    assert protected.args.authorizationId == aid
    assert protected.args.target == env.router.address
    assert protected.args.actionHash == auth["actionHash"]
    assert protected.args.resultHash == keccak(result)
    (swapped,) = env.events(env.router, "Swapped", receipt)
    assert swapped.args.sender == env.gate.address
    assert swapped.args.amountOut == evm.SWAP_EXPECTED_OUT
    assert swapped.args.recipient == env.recipient


def test_execute_resets_unused_approval(gate_env, evm):
    env = gate_env
    # Approval ceiling above what the router pulls: leftover allowance must still be cleared.
    action = env.swap_action(maxAmountIn=150 * evm.USDC)
    auth = env.authorize(action)
    _execute(env, auth, action)
    assert env.usdc.functions.allowance(env.gate.address, env.router.address).call() == 0
    assert env.balance(env.usdc, env.gate.address) == evm.GATE_USDC_FUNDING - evm.SWAP_AMOUNT_IN


def test_execute_forwards_exact_native_value(gate_env):
    env = gate_env
    target = env.deploy("RevertingTarget")
    env.send(env.gate.functions.setTargetAllowed(target.address, True), env.admin)
    action = env.call_action(target, "ok", value=12_345)
    auth = env.authorize(action)
    receipt = _execute(env, auth, action)
    assert target.functions.lastValue().call() == 12_345
    assert target.functions.calls().call() == 1
    assert env.w3.eth.get_balance(target.address) == 12_345
    (protected,) = env.events(env.gate, "ProtectedExecution", receipt)
    assert protected.args.resultHash == keccak(abi_encode(["uint256"], [1]))


# ===========================================================================
# executeAuthorized: rejections
# ===========================================================================


def test_execute_without_authorization(gate_env):
    env = gate_env
    action = env.swap_action()
    env.expect_revert(env.execute_fn(keccak(text="no such authorization"), action), env.executor,
                      "AuthorizationNotFound()")
    # A correctly computed id that was never committed onchain is equally unknown.
    auth = env.build_authorization(action)
    env.expect_revert(env.execute_fn(auth["authorizationId"], action), env.executor, "AuthorizationNotFound()")
    assert env.balance(env.tokenx, env.recipient) == 0


def test_execute_expired_authorization(gate_env):
    env = gate_env
    action = env.swap_action()
    auth = env.authorize(action, ttl=60)
    env.time_travel(auth["expiresAt"] + 1)
    assert not _is_active(env, auth["authorizationId"])
    env.expect_revert(env.execute_fn(auth["authorizationId"], action), env.executor, "AuthorizationExpired()")
    assert env.get_authorization(auth["authorizationId"])["consumed"] is False


def test_execute_at_exact_expiry_is_still_valid(gate_env, evm):
    env = gate_env
    action = env.swap_action()
    auth = env.authorize(action, ttl=60)
    env.time_travel(auth["expiresAt"])
    assert _is_active(env, auth["authorizationId"])
    _execute(env, auth, action)
    assert env.balance(env.tokenx, env.recipient) == evm.SWAP_EXPECTED_OUT


def test_execute_wrong_executor(gate_env):
    env = gate_env
    action = env.swap_action()
    auth = env.authorize(action)
    for who in (env.attacker, env.authorizer, env.admin, env.recipient):
        env.expect_revert(env.execute_fn(auth["authorizationId"], action), who, "UnauthorizedExecutor()")
    assert _is_active(env, auth["authorizationId"])


WRONG_ACTIONS = {
    "amount_maxAmountIn": lambda env, a: a.with_(maxAmountIn=a.maxAmountIn * 2),
    "amount_in_calldata": lambda env, a: a.with_(data=env.swap_calldata(amount_in=200 * 10**6)),
    "target": lambda env, a: a.with_(target=env.attacker),
    "recipient": lambda env, a: a.with_(recipient=env.attacker, data=env.swap_calldata(recipient=env.attacker)),
    "recipient_field_only": lambda env, a: a.with_(recipient=env.attacker),
    "calldata_recipient_only": lambda env, a: a.with_(data=env.swap_calldata(recipient=env.attacker)),
    "calldata_min_out": lambda env, a: a.with_(data=env.swap_calldata(min_out=0)),
    "calldata_appended_byte": lambda env, a: a.with_(data=a.data + b"\x00"),
    "assetIn": lambda env, a: a.with_(assetIn=env.tokenx.address),
    "assetOut": lambda env, a: a.with_(assetOut=env.usdc.address),
    "minAmountOut_lowered": lambda env, a: a.with_(minAmountOut=0),
    "value": lambda env, a: a.with_(value=1),
    "deadline": lambda env, a: a.with_(deadline=a.deadline + 1),
    "nonce": lambda env, a: a.with_(nonce=keccak(a.nonce)),
}


@pytest.mark.parametrize("mutation", sorted(WRONG_ACTIONS))
def test_execute_wrong_action_is_rejected(gate_env, evm, mutation):
    env = gate_env
    action = env.swap_action()
    auth = env.authorize(action)
    tampered = WRONG_ACTIONS[mutation](env, action)
    assert env.action_hash(tampered) != auth["actionHash"]
    env.expect_revert(env.execute_fn(auth["authorizationId"], tampered), env.executor, "ActionHashMismatch()",
                      value=tampered.value)
    assert _is_active(env, auth["authorizationId"])
    assert env.balance(env.usdc, env.gate.address) == evm.GATE_USDC_FUNDING
    # The genuine action still executes afterwards.
    _execute(env, auth, action)
    assert env.get_authorization(auth["authorizationId"])["consumed"] is True


def test_execute_twice_is_rejected(gate_env, evm):
    env = gate_env
    action = env.swap_action()
    auth = env.authorize(action)
    _execute(env, auth, action)
    env.expect_revert(env.execute_fn(auth["authorizationId"], action), env.executor, "AuthorizationAlreadyConsumed()")
    assert env.balance(env.tokenx, env.recipient) == evm.SWAP_EXPECTED_OUT
    assert env.balance(env.usdc, env.gate.address) == evm.GATE_USDC_FUNDING - evm.SWAP_AMOUNT_IN


def test_target_revert_leaves_authorization_unconsumed(gate_env, evm):
    env = gate_env
    target = env.deploy("RevertingTarget")
    env.send(env.gate.functions.setTargetAllowed(target.address, True), env.admin)
    action = env.swap_action(target=target.address, data=bytes.fromhex(target.encode_abi("fail", args=[])[2:]))
    auth = env.authorize(action)
    inner = evm.selector("TargetAlwaysReverts(uint256)") + abi_encode(["uint256"], [42])
    env.expect_revert(env.execute_fn(auth["authorizationId"], action), env.executor, "ExecutionFailed(bytes)",
                      args=(inner,))
    assert env.get_authorization(auth["authorizationId"])["consumed"] is False
    assert _is_active(env, auth["authorizationId"])
    # Whole transaction reverted: approval never persisted, funds untouched.
    assert env.usdc.functions.allowance(env.gate.address, target.address).call() == 0
    assert env.balance(env.usdc, env.gate.address) == evm.GATE_USDC_FUNDING


def test_router_revert_bubbles_and_leaves_authorization_unconsumed(gate_env, evm):
    env = gate_env
    action = env.swap_action()  # calldata demands >= 233.6 TOKEN-X
    auth = env.authorize(action)
    # Adverse price move before execution: the router's own minAmountOut check fails.
    env.send(env.router.functions.setRate(env.usdc.address, env.tokenx.address, 2 * 10**18), env.admin)
    inner = evm.selector("InsufficientOutput(uint256,uint256)") + abi_encode(
        ["uint256", "uint256"], [200 * evm.TOKENX, evm.SWAP_EXPECTED_OUT])
    env.expect_revert(env.execute_fn(auth["authorizationId"], action), env.executor, "ExecutionFailed(bytes)",
                      args=(inner,))
    assert _is_active(env, auth["authorizationId"])
    # Price recovers: the same authorization can still be used once.
    env.send(env.router.functions.setRate(env.usdc.address, env.tokenx.address, evm.RATE_USDC_TO_TOKENX_E18), env.admin)
    _execute(env, auth, action)
    assert env.balance(env.tokenx, env.recipient) == evm.SWAP_EXPECTED_OUT


def test_paused_rejects_and_unpause_restores(gate_env, evm):
    env = gate_env
    action = env.swap_action()
    auth = env.authorize(action)
    pending_auth = env.build_authorization(env.swap_action())

    env.expect_revert(env.gate.functions.pause(), env.attacker, ACCESS_DENIED, (env.attacker, evm.PAUSER_ROLE))
    env.expect_revert(env.gate.functions.pause(), env.admin, ACCESS_DENIED, (env.admin, evm.PAUSER_ROLE))
    env.send(env.gate.functions.pause(), env.pauser)
    assert env.gate.functions.paused().call()

    env.expect_revert(env.execute_fn(auth["authorizationId"], action), env.executor, ENFORCED_PAUSE)
    env.expect_revert(env.authorize_fn(pending_auth), env.authorizer, ENFORCED_PAUSE)
    env.expect_revert(env.gate.functions.pause(), env.pauser, ENFORCED_PAUSE)
    env.expect_revert(env.gate.functions.unpause(), env.attacker, ACCESS_DENIED, (env.attacker, evm.PAUSER_ROLE))
    assert _is_active(env, auth["authorizationId"])

    env.send(env.gate.functions.unpause(), env.pauser)
    assert not env.gate.functions.paused().call()
    env.expect_revert(env.gate.functions.unpause(), env.pauser, "ExpectedPause()")
    _execute(env, auth, action)
    env.send(env.authorize_fn(pending_auth), env.authorizer)
    assert env.balance(env.tokenx, env.recipient) == evm.SWAP_EXPECTED_OUT


def test_target_not_allowed(gate_env, evm):
    env = gate_env
    target = env.deploy("RevertingTarget")
    action = env.call_action(target, "ok")
    auth = env.authorize(action)
    env.expect_revert(env.execute_fn(auth["authorizationId"], action), env.executor, "TargetNotAllowed()")

    env.expect_revert(env.gate.functions.setTargetAllowed(target.address, True), env.attacker, ACCESS_DENIED,
                      (env.attacker, evm.DEFAULT_ADMIN_ROLE))
    env.expect_revert(env.gate.functions.setTargetAllowed(evm.ZERO_ADDRESS, True), env.admin, "InvalidTarget()")
    env.expect_revert(env.gate.functions.setTargetAllowed(env.gate.address, True), env.admin, "InvalidTarget()")
    receipt = env.send(env.gate.functions.setTargetAllowed(target.address, True), env.admin)
    (ev,) = env.events(env.gate, "TargetAllowed", receipt)
    assert ev.args.target == target.address and ev.args.allowed is True
    _execute(env, auth, action)
    assert target.functions.calls().call() == 1

    # Revocation applies to already-issued authorizations too.
    action2 = env.call_action(target, "ok")
    auth2 = env.authorize(action2)
    env.send(env.gate.functions.setTargetAllowed(target.address, False), env.admin)
    env.expect_revert(env.execute_fn(auth2["authorizationId"], action2), env.executor, "TargetNotAllowed()")


def test_action_deadline_passed(gate_env):
    env = gate_env
    action = env.swap_action(deadline=env.pending_timestamp() + 30)
    auth = env.authorize(action, ttl=300)
    env.time_travel(action.deadline + 1)
    assert _is_active(env, auth["authorizationId"])  # authorization itself not expired
    env.expect_revert(env.execute_fn(auth["authorizationId"], action), env.executor, "ActionDeadlinePassed()")


def test_value_mismatch(gate_env):
    env = gate_env
    action = env.swap_action()  # value = 0
    auth = env.authorize(action)
    env.expect_revert(env.execute_fn(auth["authorizationId"], action), env.executor, "ValueMismatch()", value=1)
    assert _is_active(env, auth["authorizationId"])


def test_reentrancy_is_blocked(gate_env, evm):
    env = gate_env
    reentrant = env.deploy("ReentrantTarget", env.gate.address)
    victim = env.deploy("RevertingTarget")
    for t in (reentrant.address, victim.address):
        env.send(env.gate.functions.setTargetAllowed(t, True), env.admin)

    # Inner authorization: fully valid, executable by the ReentrantTarget contract itself.
    inner_action = env.call_action(victim, "ok")
    inner = env.authorize(inner_action, executor=reentrant.address)

    # Outer action: the gate calls ReentrantTarget, which calls back executeAuthorized(inner).
    def outer_action(fn_name: str):
        return env.call_action(reentrant, fn_name, [inner["authorizationId"], inner_action.as_tuple()])

    # 1) Bubbling variant: the whole outer execution fails with the guard's error inside ExecutionFailed.
    outer1 = outer_action("attack")
    auth1 = env.authorize(outer1)
    guard_error = evm.selector("ReentrancyGuardReentrantCall()")
    env.expect_revert(env.execute_fn(auth1["authorizationId"], outer1), env.executor, "ExecutionFailed(bytes)",
                      args=(guard_error,))
    assert _is_active(env, auth1["authorizationId"])

    # 2) Swallowing variant: outer succeeds, but the re-entrant call was rejected by nonReentrant.
    outer2 = outer_action("attackAndRecord")
    auth2 = env.authorize(outer2)
    _execute(env, auth2, outer2)
    assert reentrant.functions.lastRevertData().call() == guard_error
    assert reentrant.functions.reentered().call() is False
    assert victim.functions.calls().call() == 0
    assert _is_active(env, inner["authorizationId"])

    # Control: outside of a gate execution the very same inner call is valid, proving the guard
    # (not some other check) is what blocked it above.
    env.send(reentrant.functions.attack(inner["authorizationId"], inner_action.as_tuple()), env.attacker)
    assert victim.functions.calls().call() == 1
    assert env.get_authorization(inner["authorizationId"])["consumed"] is True


def test_slippage_check_enforced_by_gate(gate_env, evm):
    env = gate_env
    # The router calldata accepts any output (min 0): only the gate's minAmountOut protects the swap.
    action = env.swap_action(data=env.swap_calldata(min_out=0))
    auth = env.authorize(action)
    env.send(env.router.functions.setRate(env.usdc.address, env.tokenx.address, 2 * 10**18), env.admin)
    env.expect_revert(env.execute_fn(auth["authorizationId"], action), env.executor,
                      "SlippageExceeded(uint256,uint256)", args=(200 * evm.TOKENX, evm.SWAP_EXPECTED_OUT))
    assert _is_active(env, auth["authorizationId"])
    assert env.balance(env.usdc, env.gate.address) == evm.GATE_USDC_FUNDING

    # Output is measured at the declared recipient: a swap paying someone else fails the check.
    action2 = env.swap_action(data=env.swap_calldata(min_out=0, recipient=env.other))
    auth2 = env.authorize(action2)
    env.expect_revert(env.execute_fn(auth2["authorizationId"], action2), env.executor,
                      "SlippageExceeded(uint256,uint256)", args=(0, evm.SWAP_EXPECTED_OUT))

    # Exactly minAmountOut is accepted.
    env.send(env.router.functions.setRate(env.usdc.address, env.tokenx.address, evm.RATE_USDC_TO_TOKENX_E18), env.admin)
    _execute(env, auth, action)
    assert env.balance(env.tokenx, env.recipient) == evm.SWAP_EXPECTED_OUT


# ===========================================================================
# recordReceipt
# ===========================================================================


def test_record_receipt_rules(gate_env, evm):
    env = gate_env
    receipt_hash = keccak(text="receipt")
    env.expect_revert(env.gate.functions.recordReceipt(keccak(text="unknown"), receipt_hash), env.authorizer,
                      "AuthorizationNotFound()")

    action = env.swap_action()
    auth = env.authorize(action)
    aid = auth["authorizationId"]
    env.expect_revert(env.gate.functions.recordReceipt(aid, receipt_hash), env.authorizer, "AuthorizationNotConsumed()")

    _execute(env, auth, action)
    env.expect_revert(env.gate.functions.recordReceipt(aid, receipt_hash), env.attacker, ACCESS_DENIED,
                      (env.attacker, evm.AUTHORIZER_ROLE))
    env.expect_revert(env.gate.functions.recordReceipt(aid, receipt_hash), env.executor, ACCESS_DENIED,
                      (env.executor, evm.AUTHORIZER_ROLE))
    env.expect_revert(env.gate.functions.recordReceipt(aid, evm.ZERO32), env.authorizer, "InvalidReceipt()")

    tx = env.send(env.gate.functions.recordReceipt(aid, receipt_hash), env.authorizer)
    (ev,) = env.events(env.gate, "ReceiptCommitted", tx)
    assert ev.args.authorizationId == aid and ev.args.receiptHash == receipt_hash
    assert env.gate.functions.receiptHashes(aid).call() == receipt_hash

    env.expect_revert(env.gate.functions.recordReceipt(aid, keccak(text="another")), env.authorizer,
                      "ReceiptAlreadyRecorded()")
    assert env.gate.functions.receiptHashes(aid).call() == receipt_hash


def test_record_receipt_allowed_while_paused(gate_env):
    env = gate_env
    action = env.swap_action()
    auth = env.authorize(action)
    _execute(env, auth, action)
    env.send(env.gate.functions.pause(), env.pauser)
    env.send(env.gate.functions.recordReceipt(auth["authorizationId"], keccak(text="r")), env.authorizer)


# ===========================================================================
# Admin: sweep / receive
# ===========================================================================


def test_sweep_tokens_and_native(gate_env, evm):
    env = gate_env
    env.expect_revert(env.gate.functions.sweep(env.usdc.address, env.attacker, 1), env.attacker, ACCESS_DENIED,
                      (env.attacker, evm.DEFAULT_ADMIN_ROLE))
    env.expect_revert(env.gate.functions.sweep(env.usdc.address, evm.ZERO_ADDRESS, 1), env.admin, "InvalidAddress()")

    receipt = env.send(env.gate.functions.sweep(env.usdc.address, env.other, 10 * evm.USDC), env.admin)
    assert env.balance(env.usdc, env.other) == 10 * evm.USDC
    (ev,) = env.events(env.gate, "Swept", receipt)
    assert (ev.args.token, ev.args.to, ev.args.amount) == (env.usdc.address, env.other, 10 * evm.USDC)

    # receive() accepts native value; sweep(address(0)) returns it.
    env.w3.eth.wait_for_transaction_receipt(
        env.w3.eth.send_transaction({"from": env.admin, "to": env.gate.address, "value": 10**18, "gas": 100_000}))
    assert env.w3.eth.get_balance(env.gate.address) == 10**18
    before = env.w3.eth.get_balance(env.other)
    env.send(env.gate.functions.sweep(evm.ZERO_ADDRESS, env.other, 4 * 10**17), env.admin)
    assert env.w3.eth.get_balance(env.other) == before + 4 * 10**17
    assert env.w3.eth.get_balance(env.gate.address) == 6 * 10**17


# ===========================================================================
# Demo token and router
# ===========================================================================


def test_demo_router_quote_and_errors(gate_env, evm):
    env = gate_env
    r = env.router.functions
    assert r.quote(env.usdc.address, env.tokenx.address, evm.SWAP_AMOUNT_IN).call() == evm.SWAP_EXPECTED_OUT
    assert r.quote(env.usdc.address, env.tokenx.address, 1).call() == 2_336_000_000_000  # 0.000001 USDC

    # Reverse direction with decimals going down (18 -> 6), rounded down.
    env.send(r.setRate(env.tokenx.address, env.usdc.address, 428_082_191_780_821_917), env.admin)  # ~1/2.336
    assert r.quote(env.tokenx.address, env.usdc.address, evm.SWAP_EXPECTED_OUT).call() == 99_999_999

    env.expect_revert(r.quote(env.tokenx.address, env.tokenx.address, 1), env.admin,
                      "RateNotSet(address,address)", (env.tokenx.address, env.tokenx.address))
    env.expect_revert(r.setRate(env.usdc.address, env.tokenx.address, 1), env.attacker,
                      "OwnableUnauthorizedAccount(address)", (env.attacker,))
    env.expect_revert(r.setRate(env.usdc.address, env.usdc.address, 1), env.admin, "InvalidPair()")

    # Liquidity check: a swap larger than the reserve reverts.
    big = 1_000_000 * evm.USDC
    env.send(env.usdc.functions.mint(env.other, big), env.admin)
    env.send(env.usdc.functions.approve(env.router.address, big), env.other)
    env.expect_revert(r.swapExactIn(env.usdc.address, env.tokenx.address, big, 0, env.other), env.other,
                      "InsufficientLiquidity(uint256,uint256)", (evm.ROUTER_TOKENX_RESERVE, 2_336_000 * evm.TOKENX))


def test_demo_erc20_mint_is_owner_only(gate_env):
    env = gate_env
    env.expect_revert(env.usdc.functions.mint(env.attacker, 1), env.attacker, "OwnableUnauthorizedAccount(address)",
                      (env.attacker,))
    env.send(env.usdc.functions.mint(env.other, 5), env.admin)
    assert env.balance(env.usdc, env.other) == 5
    assert env.usdc.functions.symbol().call() == "dUSDC"


# ===========================================================================
# Fuzz: hashing is injective in the fields that matter, and Python/Solidity parity holds.
# Deterministic seeded generation (no extra dependency, reproducible failures: the case index
# and seed are in the test id). Boundary values are mixed in on purpose.
# ===========================================================================

FUZZ_SEED = 0x5AF1C1A
FUZZ_CASES = 40
_UINT256_EDGES = [0, 1, 2**64 - 1, 2**128, 2**255, 2**256 - 2, 2**256 - 1]


def _rng(case: int, salt: str) -> random.Random:
    return random.Random(f"{FUZZ_SEED}:{salt}:{case}")


def _uint(rng: random.Random, bits: int = 256) -> int:
    if rng.random() < 0.2:
        return rng.choice([v for v in _UINT256_EDGES if v < 2**bits] + [2**bits - 1])
    return rng.getrandbits(rng.choice([8, 64, bits]))


def _address(rng: random.Random) -> str:
    return to_checksum_address(rng.randbytes(20))


def _calldata(rng: random.Random) -> bytes:
    return rng.randbytes(rng.choice([0, 1, 4, 31, 32, 33, 68, rng.randrange(0, 300)]))


@pytest.mark.parametrize("case", range(FUZZ_CASES))
def test_fuzz_action_hash_parity(readonly_gate_env, evm, case):
    env, rng = readonly_gate_env, _rng(case, "action")
    action = evm.Action(
        target=_address(rng), value=_uint(rng), data=_calldata(rng), assetIn=_address(rng), maxAmountIn=_uint(rng),
        assetOut=_address(rng), minAmountOut=_uint(rng), recipient=_address(rng), deadline=_uint(rng, 64),
        nonce=rng.randbytes(32),
    )
    assert env.gate.functions.computeActionHash(action.as_tuple()).call() == env.action_hash(action)


@pytest.mark.parametrize("case", range(FUZZ_CASES))
def test_fuzz_different_calldata_gives_different_action_hash(readonly_gate_env, case):
    env, rng = readonly_gate_env, _rng(case, "calldata")
    data1 = _calldata(rng)
    # A single flipped bit, a truncation/extension, or an unrelated payload.
    kind = case % 3
    if kind == 0 and data1:
        pos = rng.randrange(len(data1))
        data2 = data1[:pos] + bytes([data1[pos] ^ (1 << rng.randrange(8))]) + data1[pos + 1 :]
    elif kind == 1:
        data2 = data1 + b"\x00" if rng.random() < 0.5 or not data1 else data1[:-1]
    else:
        data2 = _calldata(rng)
        if data2 == data1:
            data2 = data1 + b"\x01"
    base = env.swap_action(nonce=b"\x11" * 32, deadline=2**40)
    h1 = env.gate.functions.computeActionHash(base.with_(data=data1).as_tuple()).call()
    h2 = env.gate.functions.computeActionHash(base.with_(data=data2).as_tuple()).call()
    assert h1 != h2
    assert h1 == env.action_hash(base.with_(data=data1))
    assert h2 == env.action_hash(base.with_(data=data2))


@pytest.mark.parametrize("case", range(FUZZ_CASES))
def test_fuzz_different_amount_gives_different_action_hash(readonly_gate_env, case):
    env, rng = readonly_gate_env, _rng(case, "amount")
    amount1 = _uint(rng)
    amount2 = (amount1 + rng.choice([1, 2**rng.randrange(256), _uint(rng) or 1])) % 2**256
    if amount2 == amount1:
        amount2 = (amount1 + 1) % 2**256
    base = env.swap_action(nonce=b"\x22" * 32, deadline=2**40)
    fn = env.gate.functions.computeActionHash
    # Amount as the approval ceiling ...
    assert fn(base.with_(maxAmountIn=amount1).as_tuple()).call() != fn(base.with_(maxAmountIn=amount2).as_tuple()).call()
    # ... as the minimum output ...
    assert fn(base.with_(minAmountOut=amount1).as_tuple()).call() != fn(base.with_(minAmountOut=amount2).as_tuple()).call()
    # ... as native value ...
    assert fn(base.with_(value=amount1).as_tuple()).call() != fn(base.with_(value=amount2).as_tuple()).call()
    # ... and as the amount encoded inside the router calldata.
    c1 = base.with_(data=env.swap_calldata(amount_in=amount1))
    c2 = base.with_(data=env.swap_calldata(amount_in=amount2))
    assert fn(c1.as_tuple()).call() != fn(c2.as_tuple()).call()


@pytest.mark.parametrize("case", range(FUZZ_CASES))
def test_fuzz_authorization_id_parity(readonly_gate_env, evm, case):
    rng = _rng(case, "auth")
    args = (rng.randbytes(32), rng.randbytes(32), rng.randbytes(32), _address(rng), rng.randbytes(32))
    onchain = readonly_gate_env.gate.functions.computeAuthorizationId(*args).call()
    assert onchain == evm.compute_authorization_id(*args)
