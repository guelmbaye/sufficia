"""Shared fixtures for the executed EvidenceGate contract tests (web3 8 + eth-tester / py-evm).

These tests deliberately do NOT import the `sufficia` package: they only read the compiled
artifacts produced by `contracts/scripts/build.mjs` from apps/api/sufficia/blockchain/artifacts.
Helpers are exposed through fixtures (`gate_env`, `evm`) rather than imports, so the tests work
under any pytest import mode and regardless of `__init__.py` files added later.

Every test gets a fresh in-memory chain (py-evm, Prague rules, which include Cancun) with:
  - Demo USDC (6 decimals) and TOKEN-X (18 decimals)
  - DemoSwapRouter at 1 USDC = 2.336 TOKEN-X, holding TOKEN-X reserves
  - EvidenceGate (admin / authorizer / pauser roles on distinct tester accounts), router allowed
  - 1000 demo USDC minted to the gate

Timing model: eth-tester auto-mines one block per transaction. A transaction executes with the
*pending* block's timestamp, so every timestamp-sensitive helper reads/uses the pending block.
`time_travel(ts)` makes the next (pending) block have timestamp exactly `ts`.
"""

from __future__ import annotations

import ast
import json
import os
import secrets
import types
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from eth_abi import encode as abi_encode
from eth_tester import EthereumTester, PyEVMBackend
from eth_tester.exceptions import TransactionFailed
from eth_utils import keccak
from web3 import EthereumTesterProvider, Web3
from web3.exceptions import ContractLogicError
from web3.logs import DISCARD

ARTIFACTS_DIR = Path(
    os.environ.get(
        "SUFFICIA_CONTRACT_ARTIFACTS",
        Path(__file__).resolve().parents[2] / "sufficia" / "blockchain" / "artifacts",
    )
)

USDC_DECIMALS = 6
TOKENX_DECIMALS = 18
USDC = 10**USDC_DECIMALS
TOKENX = 10**TOKENX_DECIMALS
RATE_USDC_TO_TOKENX_E18 = 2_336_000_000_000_000_000  # 1 USDC = 2.336 TOKEN-X
GATE_USDC_FUNDING = 1_000 * USDC
ROUTER_TOKENX_RESERVE = 1_000_000 * TOKENX
SWAP_AMOUNT_IN = 100 * USDC
SWAP_EXPECTED_OUT = 233_600_000_000_000_000_000  # 233.6 TOKEN-X

TX_GAS = 5_000_000
ZERO_ADDRESS = "0x" + "00" * 20
ZERO32 = b"\x00" * 32

# Domain constants, reproduced independently from their preimages.
ACTION_DOMAIN = keccak(text="EVIDENCE_GATE_ACTION_V1")
AUTH_DOMAIN = keccak(text="EVIDENCE_GATE_AUTH_V1")
AUTHORIZER_ROLE = keccak(text="AUTHORIZER_ROLE")
PAUSER_ROLE = keccak(text="PAUSER_ROLE")
DEFAULT_ADMIN_ROLE = ZERO32
MAX_AUTHORIZATION_TTL = 600

ACTION_HASH_TYPES = [
    "bytes32",  # ACTION_DOMAIN
    "uint256",  # chainid
    "address",  # gate
    "address",  # target
    "uint256",  # value
    "bytes32",  # keccak256(data)
    "address",  # assetIn
    "uint256",  # maxAmountIn
    "address",  # assetOut
    "uint256",  # minAmountOut
    "address",  # recipient
    "uint64",  # deadline
    "bytes32",  # nonce
]
AUTH_ID_TYPES = ["bytes32", "bytes32", "bytes32", "bytes32", "address", "bytes32"]


# ---------------------------------------------------------------------------
# Pure helpers (independent reference implementation of the onchain encodings)
# ---------------------------------------------------------------------------


def selector(signature: str) -> bytes:
    """4-byte selector of a function or custom error signature, e.g. 'ValueMismatch()'."""
    return keccak(text=signature)[:4]


def event_topic(signature: str) -> bytes:
    return keccak(text=signature)


@dataclass(frozen=True)
class Action:
    target: str
    value: int
    data: bytes
    assetIn: str
    maxAmountIn: int
    assetOut: str
    minAmountOut: int
    recipient: str
    deadline: int
    nonce: bytes

    def as_tuple(self) -> tuple:
        return (
            self.target,
            self.value,
            self.data,
            self.assetIn,
            self.maxAmountIn,
            self.assetOut,
            self.minAmountOut,
            self.recipient,
            self.deadline,
            self.nonce,
        )

    def with_(self, **changes: Any) -> "Action":
        return replace(self, **changes)


def compute_action_hash(chain_id: int, gate: str, a: Action) -> bytes:
    return keccak(
        abi_encode(
            ACTION_HASH_TYPES,
            [
                ACTION_DOMAIN,
                chain_id,
                gate,
                a.target,
                a.value,
                keccak(a.data),
                a.assetIn,
                a.maxAmountIn,
                a.assetOut,
                a.minAmountOut,
                a.recipient,
                a.deadline,
                a.nonce,
            ],
        )
    )


def compute_authorization_id(
    contract_hash: bytes, evidence_root: bytes, action_hash: bytes, executor: str, nonce: bytes
) -> bytes:
    return keccak(
        abi_encode(AUTH_ID_TYPES, [AUTH_DOMAIN, contract_hash, evidence_root, action_hash, executor, nonce])
    )


def revert_data(exc: BaseException) -> bytes:
    """Extracts raw revert bytes from whatever web3 8 / eth-tester raised.

    - Against a real node, web3 8 raises ContractCustomError / ContractLogicError with `.data` as a
      0x-hex string (sometimes a dict with a "data" key).
    - Against EthereumTesterProvider + PyEVMBackend, custom errors are NOT decoded by web3: eth-tester
      raises `eth_tester.exceptions.TransactionFailed("execution reverted: b'...'")` where the message
      is the Python repr of the raw revert bytes. We parse that repr.
    """
    data = getattr(exc, "data", None)
    if isinstance(data, dict):
        data = data.get("data")
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str) and data.startswith("0x"):
        return bytes.fromhex(data[2:])
    msg: Any = exc.args[0] if exc.args else ""
    if isinstance(msg, BaseException):
        msg = msg.args[0] if msg.args else ""
    if isinstance(msg, (bytes, bytearray)):
        return bytes(msg)
    msg = str(msg)
    prefix = "execution reverted: "
    if msg.startswith(prefix):
        msg = msg[len(prefix) :]
    if msg.startswith(("b'", 'b"')):
        value = ast.literal_eval(msg)
        if isinstance(value, bytes):
            return value
    if msg.startswith("0x"):
        return bytes.fromhex(msg[2:])
    raise AssertionError(f"could not extract revert data from {exc!r}")


REVERT_EXCEPTIONS = (TransactionFailed, ContractLogicError)


def load_artifact(name: str) -> dict:
    path = ARTIFACTS_DIR / f"{name}.json"
    if not path.exists():
        pytest.fail(
            f"Missing contract artifact {path}. Build it first: cd contracts && npm install && npm run build",
            pytrace=False,
        )
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Chain environment
# ---------------------------------------------------------------------------


class GateEnv:
    """A fresh chain with the SUFFICIA demo deployment and helpers to drive it."""

    def __init__(self) -> None:
        self.tester = EthereumTester(PyEVMBackend())
        self.w3 = Web3(EthereumTesterProvider(self.tester))
        accounts = self.w3.eth.accounts
        (
            self.admin,
            self.authorizer,
            self.pauser,
            self.executor,
            self.recipient,
            self.attacker,
            self.other,
        ) = accounts[:7]
        self.chain_id = self.w3.eth.chain_id
        self._abis: dict[str, list] = {}

        self.usdc = self.deploy("DemoERC20", "Demo USDC", "dUSDC", USDC_DECIMALS, self.admin)
        self.tokenx = self.deploy("DemoERC20", "Demo Token X", "TOKENX", TOKENX_DECIMALS, self.admin)
        self.router = self.deploy("DemoSwapRouter", self.admin)
        self.send(
            self.router.functions.setRate(self.usdc.address, self.tokenx.address, RATE_USDC_TO_TOKENX_E18),
            self.admin,
        )
        self.send(self.tokenx.functions.mint(self.router.address, ROUTER_TOKENX_RESERVE), self.admin)
        self.gate = self.deploy("EvidenceGate", self.admin, self.authorizer, self.pauser, [self.router.address])
        self.send(self.usdc.functions.mint(self.gate.address, GATE_USDC_FUNDING), self.admin)

        self._error_by_selector = self._index_errors()

    # -- deployment / transactions ------------------------------------------------

    def deploy(self, name: str, *args: Any, sender: str | None = None):
        artifact = load_artifact(name)
        self._abis[name] = artifact["abi"]
        factory = self.w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
        tx_hash = factory.constructor(*args).transact({"from": sender or self.admin, "gas": 8_000_000})
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        assert receipt.status == 1, f"deployment of {name} failed"
        return self.w3.eth.contract(address=receipt.contractAddress, abi=artifact["abi"])

    def expect_deploy_revert(self, name: str, error_signature: str, *args: Any) -> bytes:
        """Asserts that deploying `name` with `args` reverts with the given custom error."""
        artifact = load_artifact(name)
        factory = self.w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
        tx = factory.constructor(*args).build_transaction({"from": self.admin, "gas": 8_000_000})
        with pytest.raises(REVERT_EXCEPTIONS) as exc_info:
            self.w3.eth.call({"from": self.admin, "data": tx["data"], "gas": 8_000_000}, "pending")
        data = revert_data(exc_info.value)
        assert data[:4] == selector(error_signature), f"expected {error_signature}, got 0x{data.hex()}"
        return data

    def _index_errors(self) -> dict[bytes, str]:
        out: dict[bytes, str] = {}
        for abi in self._abis.values():
            for item in abi:
                if item.get("type") == "error":
                    sig = f"{item['name']}({','.join(_abi_type(i) for i in item['inputs'])})"
                    out[selector(sig)] = sig
        return out

    def describe_revert(self, data: bytes) -> str:
        sig = self._error_by_selector.get(data[:4])
        return f"{sig or 'unknown error'} data=0x{data.hex()}"

    def send(self, fn, sender: str, value: int = 0):
        """Sends a transaction that must succeed. A pending-block eth_call runs first so that a
        failure reports the decoded custom error instead of a bare status 0."""
        tx = {"from": sender, "value": value, "gas": TX_GAS}
        try:
            fn.call(tx, block_identifier="pending")
        except REVERT_EXCEPTIONS as exc:
            raise AssertionError(f"transaction would revert: {self.describe_revert(revert_data(exc))}") from exc
        receipt = self.w3.eth.wait_for_transaction_receipt(fn.transact(tx))
        assert receipt.status == 1, "transaction reverted onchain"
        return receipt

    def expect_revert(
        self,
        fn,
        sender: str,
        error_signature: str,
        args: tuple | list | None = None,
        value: int = 0,
    ) -> bytes:
        """Asserts that `fn` reverts with the given custom error (by 4-byte selector, and by ABI-encoded
        arguments when `args` is given), both in eth_call and as a mined transaction (status 0).
        Returns the raw revert data."""
        tx = {"from": sender, "value": value, "gas": TX_GAS}
        with pytest.raises(REVERT_EXCEPTIONS) as exc_info:
            fn.call(tx, block_identifier="pending")
        data = revert_data(exc_info.value)
        expected = selector(error_signature)
        assert data[:4] == expected, (
            f"expected {error_signature} (0x{expected.hex()}), got {self.describe_revert(data)}"
        )
        if args is not None:
            types = _signature_types(error_signature)
            assert data[4:] == abi_encode(types, list(args)), f"unexpected error args: {self.describe_revert(data)}"
        # The same transaction, actually mined, must fail too (explicit gas skips estimation).
        receipt = self.w3.eth.wait_for_transaction_receipt(fn.transact(tx))
        assert receipt.status == 0, "transaction unexpectedly succeeded onchain"
        return data

    def events(self, contract, event_name: str, receipt) -> list:
        return list(getattr(contract.events, event_name)().process_receipt(receipt, errors=DISCARD))

    # -- time ---------------------------------------------------------------------

    def pending_timestamp(self) -> int:
        return self.tester.get_block_by_number("pending")["timestamp"]

    def time_travel(self, timestamp: int) -> None:
        """The next mined transaction will execute with block.timestamp == `timestamp`."""
        self.tester.time_travel(timestamp)
        assert self.pending_timestamp() == timestamp

    # -- domain helpers -----------------------------------------------------------

    def action_hash(self, action: Action, gate: str | None = None) -> bytes:
        return compute_action_hash(self.chain_id, gate or self.gate.address, action)

    def swap_calldata(
        self,
        amount_in: int = SWAP_AMOUNT_IN,
        min_out: int = SWAP_EXPECTED_OUT,
        recipient: str | None = None,
        token_in: str | None = None,
        token_out: str | None = None,
    ) -> bytes:
        return bytes.fromhex(
            self.router.encode_abi(
                "swapExactIn",
                args=[
                    token_in or self.usdc.address,
                    token_out or self.tokenx.address,
                    amount_in,
                    min_out,
                    recipient or self.recipient,
                ],
            )[2:]
        )

    def swap_action(self, **overrides: Any) -> Action:
        """The canonical demo action: swap 100 dUSDC held by the gate into >= 233.6 TOKEN-X."""
        action = Action(
            target=self.router.address,
            value=0,
            data=self.swap_calldata(),
            assetIn=self.usdc.address,
            maxAmountIn=SWAP_AMOUNT_IN,
            assetOut=self.tokenx.address,
            minAmountOut=SWAP_EXPECTED_OUT,
            recipient=self.recipient,
            deadline=self.pending_timestamp() + 3_600,
            nonce=secrets.token_bytes(32),
        )
        return action.with_(**overrides) if overrides else action

    def call_action(self, contract, fn_name: str, args: list | None = None, value: int = 0, **overrides: Any) -> Action:
        """A plain contract call with no token movement (no approval, no output check)."""
        data = bytes.fromhex(contract.encode_abi(fn_name, args=args or [])[2:])
        return self.swap_action(
            target=contract.address,
            value=value,
            data=data,
            assetIn=ZERO_ADDRESS,
            maxAmountIn=0,
            assetOut=ZERO_ADDRESS,
            minAmountOut=0,
            **overrides,
        )

    def register_intent(self, intent_id: bytes | None = None, contract_hash: bytes | None = None):
        intent_id = intent_id or keccak(text="intent:" + secrets.token_hex(8))
        contract_hash = contract_hash or keccak(text="contract:" + secrets.token_hex(8))
        self.send(self.gate.functions.registerIntent(intent_id, contract_hash), self.authorizer)
        return intent_id, contract_hash

    def build_authorization(
        self,
        action: Action,
        *,
        executor: str | None = None,
        ttl: int = 300,
        intent_id: bytes | None = None,
        contract_hash: bytes | None = None,
        evidence_root: bytes | None = None,
        nonce: bytes | None = None,
        register: bool = True,
    ) -> dict:
        """Builds authorize() arguments exactly like the backend would (all hashes computed in Python)."""
        if register:
            intent_id, contract_hash = self.register_intent(intent_id, contract_hash)
        executor = executor or self.executor
        evidence_root = evidence_root or keccak(text="evidence:" + secrets.token_hex(8))
        nonce = nonce or action.nonce
        action_hash = self.action_hash(action)
        issued_at = self.pending_timestamp()
        return {
            "authorizationId": compute_authorization_id(contract_hash, evidence_root, action_hash, executor, nonce),
            "intentId": intent_id,
            "contractHash": contract_hash,
            "evidenceRoot": evidence_root,
            "actionHash": action_hash,
            "executor": executor,
            "issuedAt": issued_at,
            "expiresAt": issued_at + ttl,
            "nonce": nonce,
        }

    def authorize_fn(self, auth: dict):
        return self.gate.functions.authorize(
            auth["authorizationId"],
            auth["intentId"],
            auth["contractHash"],
            auth["evidenceRoot"],
            auth["actionHash"],
            auth["executor"],
            auth["issuedAt"],
            auth["expiresAt"],
            auth["nonce"],
        )

    def authorize(self, action: Action, **kwargs: Any) -> dict:
        auth = self.build_authorization(action, **kwargs)
        auth["receipt"] = self.send(self.authorize_fn(auth), self.authorizer)
        return auth

    def execute_fn(self, authorization_id: bytes, action: Action):
        return self.gate.functions.executeAuthorized(authorization_id, action.as_tuple())

    def get_authorization(self, authorization_id: bytes) -> dict:
        fields = self.gate.functions.getAuthorization(authorization_id).call()
        keys = ["intentId", "contractHash", "evidenceRoot", "actionHash", "executor", "issuedAt", "expiresAt", "consumed"]
        return dict(zip(keys, fields))

    def balance(self, token, holder: str) -> int:
        return token.functions.balanceOf(holder).call()


def _abi_type(param: dict) -> str:
    t = param["type"]
    if t.startswith("tuple"):
        return "(" + ",".join(_abi_type(c) for c in param["components"]) + ")" + t[len("tuple") :]
    return t


def _signature_types(signature: str) -> list[str]:
    inner = signature[signature.index("(") + 1 : -1]
    return [t for t in inner.split(",") if t]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def gate_env() -> GateEnv:
    """A fresh chain + demo deployment per test (fast: py-evm in memory)."""
    return GateEnv()


@pytest.fixture(scope="module")
def readonly_gate_env() -> GateEnv:
    """Shared deployment for tests that only issue eth_calls (e.g. fuzzing view functions)."""
    return GateEnv()


@pytest.fixture(scope="session")
def evm() -> types.SimpleNamespace:
    """Pure helpers and constants (independent Python reference of the onchain encodings)."""
    return types.SimpleNamespace(
        Action=Action,
        compute_action_hash=compute_action_hash,
        compute_authorization_id=compute_authorization_id,
        selector=selector,
        event_topic=event_topic,
        revert_data=revert_data,
        REVERT_EXCEPTIONS=REVERT_EXCEPTIONS,
        ACTION_DOMAIN=ACTION_DOMAIN,
        AUTH_DOMAIN=AUTH_DOMAIN,
        AUTHORIZER_ROLE=AUTHORIZER_ROLE,
        PAUSER_ROLE=PAUSER_ROLE,
        DEFAULT_ADMIN_ROLE=DEFAULT_ADMIN_ROLE,
        MAX_AUTHORIZATION_TTL=MAX_AUTHORIZATION_TTL,
        ACTION_HASH_TYPES=ACTION_HASH_TYPES,
        AUTH_ID_TYPES=AUTH_ID_TYPES,
        USDC=USDC,
        TOKENX=TOKENX,
        RATE_USDC_TO_TOKENX_E18=RATE_USDC_TO_TOKENX_E18,
        GATE_USDC_FUNDING=GATE_USDC_FUNDING,
        ROUTER_TOKENX_RESERVE=ROUTER_TOKENX_RESERVE,
        SWAP_AMOUNT_IN=SWAP_AMOUNT_IN,
        SWAP_EXPECTED_OUT=SWAP_EXPECTED_OUT,
        ZERO_ADDRESS=ZERO_ADDRESS,
        ZERO32=ZERO32,
    )
