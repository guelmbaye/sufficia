"""Web3 implementation of :class:`~sufficia.blockchain.gateway.ChainGateway`.

One code path for every chain mode: transactions are ALWAYS signed locally with an ``eth_account``
LocalAccount and broadcast with ``eth_sendRawTransaction`` -- identical for the in-process LOCAL_EVM chain
and for Monad testnet / mainnet. Nothing ever relies on node-managed ("unlocked") accounts.

Transaction pipeline (:class:`TransactionSender`):
  1. one lock per signer address (a single transaction queue per signer, held until the receipt arrives
     or the confirmation timeout expires), so nonces never race;
  2. clock hook (LOCAL_EVM only: the pending block is aligned with wall-clock time before every tx);
  3. optional ``eth_call`` preflight at ``simulate_block`` (LOCAL_EVM: "pending", the exact execution
     context), then ``eth_estimateGas``: a revert in either is decoded into the custom error and returned as
     ``TxResult(status=REVERTED, tx_hash=None, error_name=...)`` WITHOUT sending anything (fail closed);
     gas limit = estimate * 1.25;
  4. EIP-1559 fees: priority = ``eth_maxPriorityFeePerGas`` (fallback 1 gwei), max fee = 2 * baseFee +
     priority; a chain without ``baseFeePerGas`` gets a legacy ``gasPrice`` transaction;
  5. nonce = ``eth_getTransactionCount(addr, "pending")``; sign; the tx hash is known before broadcast;
  6. wait for the receipt up to ``tx_confirmation_timeout_seconds``. Timeout (or the RPC disappearing
     after the broadcast) -> status SUBMITTED with the tx hash: callers reconcile with
     :meth:`Web3Gateway.find_transaction` and must never resubmit blindly;
  7. status 0 receipt -> REVERTED; the revert reason is recovered by replaying the call with
     ``eth_call`` at the receipt's block.

Status semantics: CONFIRMED (mined, status 1), REVERTED (estimation revert with ``tx_hash=None``, or
mined with status 0), SUBMITTED (broadcast, outcome unknown yet), FAILED (never submitted: RPC down,
rejected by the node, missing key...). No path turns an unknown outcome into CONFIRMED.

RPC failover (:class:`FailoverProvider`): ordered endpoints ``[primary, fallback, ...]``. On a connection
error (``requests`` ConnectionError / Timeout, HTTP 5xx or 429, socket errors) the request is retried
once on the same endpoint, then the next endpoint is tried; the provider stays on the endpoint that
answered and re-tries the primary first after ``recheck_primary_after`` seconds. When every endpoint
fails, :class:`~sufficia.blockchain.gateway.ChainUnavailable` is raised. JSON-RPC errors (a node that
answered) never trigger failover.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.parse import urlsplit

import requests
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_utils import keccak, to_checksum_address
from web3 import HTTPProvider, Web3
from web3.exceptions import (
    BadResponseFormat,
    BlockNotFound,
    ProviderConnectionError,
    RequestTimedOut,
    TooManyRequests,
    TransactionNotFound,
)
from web3.logs import DISCARD
from web3.providers.base import BaseProvider

from ..canonical.hashes import unhex, unhex32
from ..domain.enums import ChainMode, TxPurpose, TxStatus
from ..domain.models import Authorization, CanonicalAction
from .artifacts import abi_type, contract_abi, decode_exception, describe_revert, is_revert_error, jsonable
from .gateway import ChainInfo, ChainUnavailable, OnchainAuthorization, TxResult

log = logging.getLogger(__name__)

GWEI = 10**9
DEFAULT_PRIORITY_FEE = 1 * GWEI
GAS_MULTIPLIER = 1.25
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
ZERO32 = b"\x00" * 32
AUTHORIZER_ROLE = keccak(text="AUTHORIZER_ROLE")
PAUSER_ROLE = keccak(text="PAUSER_ROLE")

#: EvidenceGate events decoded into ``TxResult.events``.
GATE_EVENTS: tuple[str, ...] = (
    "IntentRegistered",
    "ExecutionAuthorized",
    "AuthorizationConsumed",
    "ProtectedExecution",
    "ReceiptCommitted",
    "TargetAllowed",
    "Swept",
    "Paused",
    "Unpaused",
)

#: Gate function selectors -> purpose (used by find_transaction when the caller gives none).
_PURPOSE_BY_FUNCTION = {
    "registerIntent": TxPurpose.INTENT_REGISTER,
    "authorize": TxPurpose.AUTHORIZATION,
    "executeAuthorized": TxPurpose.PROTECTED_EXECUTION,
    "recordReceipt": TxPurpose.RECEIPT_COMMITMENT,
}

_ALREADY_KNOWN = ("already known", "known transaction", "already imported", "alreadyknown")


class ConfigurationError(ValueError):
    """The chain configuration is incomplete or inconsistent. Never silently replaced by a fallback."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hexstr(value: Any) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()
    text = str(value)
    return text if text.startswith("0x") else "0x" + text


def redact_url(url: str) -> str:
    """scheme://host[:port] only (RPC paths / query strings often embed API keys)."""
    try:
        parts = urlsplit(url)
        if parts.scheme and parts.netloc:
            host = parts.hostname or ""
            port = f":{parts.port}" if parts.port else ""
            return f"{parts.scheme}://{host}{port}"
    except ValueError:  # pragma: no cover - malformed URL
        pass
    return "<rpc>"


# =============================================================================================
# RPC failover
# =============================================================================================


def is_connection_error(exc: BaseException) -> bool:
    """Transport-level failures that justify retrying / failing over (never JSON-RPC errors)."""
    if isinstance(exc, ChainUnavailable):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        return status is None or status >= 500 or status in (408, 429)
    if isinstance(
        exc,
        (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ChunkedEncodingError,
            ProviderConnectionError,
            RequestTimedOut,
            TooManyRequests,
            BadResponseFormat,
            json.JSONDecodeError,
            ConnectionError,
            TimeoutError,
        ),
    ):
        return True
    return False


class FailoverProvider(BaseProvider):
    """Ordered list of providers; see the module docstring for the retry / failover policy."""

    def __init__(
        self,
        providers: Sequence[BaseProvider],
        *,
        labels: Sequence[str] | None = None,
        retries_per_provider: int = 1,
        recheck_primary_after: float = 60.0,
        retry_delay: float = 0.0,
    ) -> None:
        super().__init__()
        if not providers:
            raise ConfigurationError("at least one RPC provider is required")
        self.providers = list(providers)
        self.labels = list(labels) if labels else [f"provider-{i}" for i in range(len(self.providers))]
        self.retries_per_provider = max(0, int(retries_per_provider))
        self.recheck_primary_after = recheck_primary_after
        self.retry_delay = retry_delay
        self._active = 0
        self._switched_at: float | None = None
        self._state_lock = threading.Lock()
        self.last_error: str | None = None
        self.failovers = 0

    @property
    def active_index(self) -> int:
        return self._active

    @property
    def active_label(self) -> str:
        return self.labels[self._active]

    def _start_index(self) -> int:
        with self._state_lock:
            if (
                self._active != 0
                and self._switched_at is not None
                and time.monotonic() - self._switched_at >= self.recheck_primary_after
            ):
                return 0  # give the primary another chance
            return self._active

    def _mark(self, index: int) -> None:
        with self._state_lock:
            if index != self._active:
                if index != 0:
                    self.failovers += 1
                    log.warning("rpc failover: %s -> %s", self.labels[self._active], self.labels[index])
                else:
                    log.info("rpc: back on primary %s", self.labels[0])
                self._active = index
                self._switched_at = time.monotonic() if index != 0 else None
            elif index != 0:
                self._switched_at = self._switched_at or time.monotonic()

    def make_request(self, method: Any, params: Any) -> Any:
        start = self._start_index()
        order = [start] + [i for i in range(len(self.providers)) if i != start]
        errors: list[str] = []
        for index in order:
            provider = self.providers[index]
            for attempt in range(1 + self.retries_per_provider):
                try:
                    response = provider.make_request(method, params)
                except Exception as exc:  # noqa: BLE001 - classified below
                    if not is_connection_error(exc):
                        raise
                    errors.append(f"{self.labels[index]}#{attempt + 1}: {type(exc).__name__}: {exc}")
                    if self.retry_delay and attempt < self.retries_per_provider:
                        time.sleep(self.retry_delay)
                    continue
                self._mark(index)
                return response
        self.last_error = "; ".join(errors)
        raise ChainUnavailable(f"no RPC endpoint reachable for {method}: {self.last_error}")

    def is_connected(self, show_traceback: bool = False) -> bool:
        for provider in self.providers:
            try:
                if provider.is_connected(show_traceback=False):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False


def default_w3_factory(provider: BaseProvider) -> Web3:
    """Web3 for remote RPCs. The POA extraData middleware makes block decoding tolerant to extraData
    longer than 32 bytes (the field is never used by SUFFICIA)."""
    from web3.middleware import ExtraDataToPOAMiddleware

    w3 = Web3(provider)
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def http_providers(urls: Sequence[Optional[str]], *, timeout: float = 10.0) -> tuple[list[BaseProvider], list[str]]:
    """HTTPProviders without web3's built-in retry loop (the FailoverProvider owns retries)."""
    providers: list[BaseProvider] = []
    labels: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        providers.append(HTTPProvider(url, request_kwargs={"timeout": timeout}, exception_retry_configuration=None))
        labels.append(redact_url(url))
    if not providers:
        raise ConfigurationError("no RPC URL configured")
    return providers, labels


# =============================================================================================
# Transaction pipeline
# =============================================================================================

_SIGNER_LOCKS: dict[tuple[int, str], threading.Lock] = {}
_SIGNER_LOCKS_GUARD = threading.Lock()


def signer_lock(chain_id: int, address: str) -> threading.Lock:
    """Process-wide lock per (chain, signer): one transaction queue per signer."""
    key = (int(chain_id), address.lower())
    with _SIGNER_LOCKS_GUARD:
        lock = _SIGNER_LOCKS.get(key)
        if lock is None:
            lock = _SIGNER_LOCKS[key] = threading.Lock()
        return lock


def as_account(key: str | LocalAccount) -> LocalAccount:
    if isinstance(key, LocalAccount):
        return key
    return Account.from_key(key)


@dataclass
class SendOutcome:
    status: TxStatus
    from_address: str
    to_address: Optional[str]
    tx_hash: Optional[str] = None
    receipt: Any = None
    error_name: Optional[str] = None
    error_args: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    submitted_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    gas_limit: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.status == TxStatus.CONFIRMED

    @property
    def contract_address(self) -> Optional[str]:
        if self.receipt is None:
            return None
        address = self.receipt.get("contractAddress")
        return to_checksum_address(address) if address else None


class TransactionSender:
    """Signs locally, estimates (fail closed), sends raw, waits (bounded). Shared by gateway and deployer."""

    def __init__(
        self,
        w3: Web3,
        chain_id: int,
        *,
        tx_timeout: float = 30.0,
        poll_interval: float = 0.25,
        gas_multiplier: float = GAS_MULTIPLIER,
        clock_sync: Optional[Callable[[], Any]] = None,
        simulate_block: Optional[str] = None,
    ) -> None:
        self.w3 = w3
        self.chain_id = int(chain_id)
        self.tx_timeout = float(tx_timeout)
        self.poll_interval = float(poll_interval)
        self.gas_multiplier = gas_multiplier
        self.clock_sync = clock_sync
        # Optional eth_call preflight at this block tag before estimating. LOCAL_EVM uses "pending": eth-tester
        # estimates against "latest" with a wall-clock timestamp, while the tx executes with the (possibly
        # time-travelled) pending block's timestamp.
        self.simulate_block = simulate_block
        self._chain_checked = False

    # -- helpers -------------------------------------------------------------------------------

    def check_chain(self) -> Optional[str]:
        """Returns an error message if the RPC serves another chain than the one we sign for."""
        if self._chain_checked:
            return None
        rpc_chain = int(self.w3.eth.chain_id)
        if rpc_chain != self.chain_id:
            return f"RPC serves chain {rpc_chain}, expected {self.chain_id}"
        self._chain_checked = True
        return None

    def fee_fields(self) -> dict[str, int]:
        try:
            priority = int(self.w3.eth.max_priority_fee)
        except ChainUnavailable:
            raise
        except Exception:  # noqa: BLE001 - method unsupported by the node
            priority = DEFAULT_PRIORITY_FEE
        block = self.w3.eth.get_block("latest")
        base_fee = block.get("baseFeePerGas")
        if base_fee is not None:
            return {"type": 2, "maxPriorityFeePerGas": priority, "maxFeePerGas": 2 * int(base_fee) + priority}
        return {"gasPrice": int(self.w3.eth.gas_price)}

    def estimate(self, call: dict[str, Any]) -> int:
        return int(math.ceil(int(self.w3.eth.estimate_gas(call)) * self.gas_multiplier))

    def replay_revert(self, call: dict[str, Any], block_number: Optional[int]) -> tuple[Optional[str], dict[str, Any]]:
        """Re-executes a mined, reverted transaction with eth_call at its block to recover the error."""
        if block_number is None:
            return None, {}
        try:
            self.w3.eth.call(call, block_number)
        except Exception as exc:  # noqa: BLE001
            if is_revert_error(exc):
                name, args, _ = decode_exception(exc)
                return name, args
        return None, {}

    def _node_knows(self, tx_hash: str) -> bool:
        try:
            return self.w3.eth.get_transaction(tx_hash) is not None
        except Exception:  # noqa: BLE001
            return False

    def wait_for_receipt(self, tx_hash: str, timeout: Optional[float] = None) -> Any:
        """Polls for a receipt; tolerant to transient RPC loss. Returns None on timeout."""
        deadline = time.monotonic() + (self.tx_timeout if timeout is None else timeout)
        while True:
            try:
                return self.w3.eth.get_transaction_receipt(tx_hash)
            except TransactionNotFound:
                pass
            except Exception as exc:  # noqa: BLE001 - RPC loss / node hiccup: keep polling until the deadline
                log.warning("receipt poll for %s: %s: %s", tx_hash, type(exc).__name__, exc)
            if time.monotonic() >= deadline:
                return None
            time.sleep(self.poll_interval)

    # -- send ----------------------------------------------------------------------------------

    def send(
        self,
        account: LocalAccount,
        *,
        to: Optional[str],
        data: str | bytes,
        value: int = 0,
        gas: Optional[int] = None,
        sync_clock: bool = False,
        timeout: Optional[float] = None,
    ) -> SendOutcome:
        sender = account.address
        to_address = to_checksum_address(to) if to else None
        data_hex = hexstr(data) if data else "0x"
        outcome = SendOutcome(status=TxStatus.FAILED, from_address=sender, to_address=to_address)
        with signer_lock(self.chain_id, sender):
            try:
                if sync_clock and self.clock_sync is not None:
                    self.clock_sync()
                mismatch = self.check_chain()
                if mismatch:
                    outcome.error_name, outcome.error = "ChainIdMismatch", mismatch
                    return outcome
                call: dict[str, Any] = {"from": sender, "data": data_hex, "value": int(value)}
                if to_address:
                    call["to"] = to_address
                if gas is None:  # an explicit gas limit skips simulation (tests use it to mine a revert)
                    steps: list[tuple[str, Callable[[], Any]]] = []
                    if self.simulate_block is not None:
                        steps.append(("simulation", lambda: self.w3.eth.call(call, self.simulate_block)))
                    steps.append(("gas estimation", lambda: self.estimate(call)))
                    for label, step in steps:
                        try:
                            result = step()
                        except ChainUnavailable:
                            raise
                        except Exception as exc:  # noqa: BLE001
                            if is_revert_error(exc):
                                name, args, _ = decode_exception(exc)
                                outcome.status = TxStatus.REVERTED
                                outcome.error_name, outcome.error_args = name, args
                                outcome.error = f"{label} reverted (not sent): " + describe_revert(name, args)
                            else:
                                outcome.error_name = type(exc).__name__
                                outcome.error = f"{label} failed (not sent): {exc}"
                            return outcome
                        if label == "gas estimation":
                            gas = int(result)
                outcome.gas_limit = int(gas)
                tx: dict[str, Any] = {
                    "chainId": self.chain_id,
                    "nonce": int(self.w3.eth.get_transaction_count(sender, "pending")),
                    "gas": int(gas),
                    "value": int(value),
                    "data": data_hex,
                    **self.fee_fields(),
                }
                if to_address:
                    tx["to"] = to_address
                signed = account.sign_transaction(tx)
            except ChainUnavailable as exc:
                outcome.error_name, outcome.error = "ChainUnavailable", f"not sent: {exc}"
                return outcome
            except Exception as exc:  # noqa: BLE001 - nothing was broadcast: FAILED, never an exception
                outcome.error_name = type(exc).__name__
                outcome.error = f"not sent: {exc}"
                return outcome

            tx_hash = hexstr(signed.hash)
            outcome.tx_hash = tx_hash
            outcome.submitted_at = utcnow()
            try:
                self.w3.eth.send_raw_transaction(signed.raw_transaction)
            except ChainUnavailable as exc:
                # Delivery unknown (the request may have reached a node): reconcile, never resend blindly.
                outcome.status = TxStatus.SUBMITTED
                outcome.error_name = "ChainUnavailable"
                outcome.error = f"broadcast not acknowledged, reconcile with find_transaction: {exc}"
                return outcome
            except Exception as exc:  # noqa: BLE001
                message = str(getattr(exc, "message", None) or exc).lower()
                # "already known" (or e.g. "nonce too low" after a retried broadcast that did land): if the
                # node knows our exact signed transaction, wait for it instead of reporting a failure.
                if not (any(marker in message for marker in _ALREADY_KNOWN) or self._node_knows(tx_hash)):
                    if is_revert_error(exc):
                        name, args, _ = decode_exception(exc)
                        outcome.error_name, outcome.error_args = name, args
                        outcome.error = "rejected by the node: " + describe_revert(name, args)
                    else:
                        outcome.error_name = type(exc).__name__
                        outcome.error = f"rejected by the node: {exc}"
                    outcome.tx_hash = None
                    outcome.submitted_at = None
                    return outcome

            receipt = self.wait_for_receipt(tx_hash, timeout)
            if receipt is None:
                outcome.status = TxStatus.SUBMITTED
                outcome.error = (
                    f"no receipt after {self.tx_timeout if timeout is None else timeout:.1f}s; "
                    "reconcile with find_transaction (do not resubmit)"
                )
                return outcome
            outcome.receipt = receipt
            outcome.confirmed_at = utcnow()
            if int(receipt.get("status", 0)) == 1:
                outcome.status = TxStatus.CONFIRMED
            else:
                outcome.status = TxStatus.REVERTED
                replay = dict(call)
                replay["gas"] = int(gas)
                name, args = self.replay_revert(replay, receipt.get("blockNumber"))
                outcome.error_name, outcome.error_args = name, args
                outcome.error = "transaction reverted onchain: " + describe_revert(name, args)
            return outcome


# =============================================================================================
# Gateway
# =============================================================================================


class Web3Gateway:
    """ChainGateway over any web3 provider (HTTP with failover for Monad, in-process py-evm locally).

    Build with ``w3=`` (an existing Web3), ``providers=`` (wrapped in a :class:`FailoverProvider`) or
    ``Web3Gateway.from_rpc_urls(...)``. ``signer_keys`` maps roles (``authorizer``, ``executor``,
    ``pauser``, ``deployer``) to private keys; keys never leave this object.
    """

    def __init__(
        self,
        *,
        info: ChainInfo,
        signer_keys: Mapping[str, Optional[str]],
        w3: Optional[Web3] = None,
        providers: Optional[Sequence[BaseProvider]] = None,
        provider_labels: Optional[Sequence[str]] = None,
        w3_factory: Optional[Callable[[BaseProvider], Web3]] = None,
        tx_timeout: float = 30.0,
        poll_interval: float = 0.25,
        clock_sync: Optional[Callable[[], Any]] = None,
        view_block: str = "latest",
        simulate_block: Optional[str] = None,
        verify_signers: bool = True,
    ) -> None:
        if w3 is None:
            if not providers:
                raise ConfigurationError("Web3Gateway needs either w3= or providers=")
            provider = FailoverProvider(providers, labels=provider_labels)
            w3 = (w3_factory or default_w3_factory)(provider)
        self.w3 = w3
        self.info = info
        self.view_block = view_block
        self._clock_sync = clock_sync
        self._pending_supported: Optional[bool] = None
        self._signers: dict[str, LocalAccount] = {}
        for role, key in signer_keys.items():
            if key:
                try:
                    self._signers[role] = as_account(key)
                except Exception as exc:  # noqa: BLE001
                    raise ConfigurationError(f"invalid private key for role '{role}': {type(exc).__name__}") from None
        if verify_signers:
            for role, expected in (("authorizer", info.authorizer), ("executor", info.executor)):
                account = self._signers.get(role)
                if account is not None and expected and account.address.lower() != expected.lower():
                    raise ConfigurationError(
                        f"{role} key controls {account.address} but the chain info expects {expected}"
                    )
        self.sender = TransactionSender(
            w3,
            info.chain_id,
            tx_timeout=tx_timeout,
            poll_interval=poll_interval,
            clock_sync=clock_sync,
            simulate_block=simulate_block,
        )
        self.gate = w3.eth.contract(address=to_checksum_address(info.gate_address), abi=contract_abi("EvidenceGate"))
        self._erc20_abi = contract_abi("DemoERC20")
        self._gate_events = [getattr(self.gate.events, name) for name in GATE_EVENTS if _has_event(self.gate, name)]
        self._selectors = {_fn_selector(self.gate, fn): purpose for fn, purpose in _PURPOSE_BY_FUNCTION.items()}

    # -- construction helpers ------------------------------------------------------------------

    @classmethod
    def from_rpc_urls(
        cls,
        urls: Sequence[Optional[str]],
        *,
        info: ChainInfo,
        signer_keys: Mapping[str, Optional[str]],
        request_timeout: float = 10.0,
        **kwargs: Any,
    ) -> "Web3Gateway":
        providers, labels = http_providers(urls, timeout=request_timeout)
        return cls(info=info, signer_keys=signer_keys, providers=providers, provider_labels=labels, **kwargs)

    @property
    def provider(self) -> BaseProvider:
        return self.w3.provider

    def signer(self, role: str) -> Optional[LocalAccount]:
        return self._signers.get(role)

    def signer_address(self, role: str) -> Optional[str]:
        account = self._signers.get(role)
        return account.address if account else None

    # -- internal plumbing -------------------------------------------------------------------------

    def _sync(self) -> None:
        if self._clock_sync is not None:
            self._clock_sync()

    def _decode_events(self, receipt: Any) -> list[dict[str, Any]]:
        gate = self.gate.address.lower()
        own_logs = [lg for lg in receipt.get("logs", []) if str(lg.get("address", "")).lower() == gate]
        if not own_logs:
            return []
        filtered = dict(receipt)
        filtered["logs"] = own_logs
        events: list[dict[str, Any]] = []
        for event in self._gate_events:
            for entry in event().process_receipt(filtered, errors=DISCARD):
                events.append(
                    {
                        "event": entry["event"],
                        "args": {k: jsonable(v) for k, v in dict(entry["args"]).items()},
                        "log_index": int(entry["logIndex"]),
                    }
                )
        events.sort(key=lambda e: e["log_index"])
        return events

    def _block_timestamp_of(self, block_number: Optional[int]) -> Optional[int]:
        if block_number is None:
            return None
        try:
            return int(self.w3.eth.get_block(block_number)["timestamp"])
        except Exception:  # noqa: BLE001 - informational only
            return None

    def _to_result(self, purpose: TxPurpose, outcome: SendOutcome) -> TxResult:
        receipt = outcome.receipt
        block_number = int(receipt["blockNumber"]) if receipt is not None else None
        events: list[dict[str, Any]] = []
        if receipt is not None and outcome.status == TxStatus.CONFIRMED:
            events = self._decode_events(receipt)
        return TxResult(
            purpose=purpose,
            status=outcome.status,
            tx_hash=outcome.tx_hash,
            from_address=outcome.from_address,
            to_address=outcome.to_address,
            block_number=block_number,
            block_timestamp=self._block_timestamp_of(block_number),
            gas_used=int(receipt["gasUsed"]) if receipt is not None else None,
            error_name=outcome.error_name,
            error=outcome.error,
            events=events,
            submitted_at=outcome.submitted_at,
            confirmed_at=outcome.confirmed_at,
            explorer_url=self.explorer_tx_url(outcome.tx_hash),
        )

    def _failed(self, purpose: TxPurpose, from_address: str, error_name: str, error: str) -> TxResult:
        return TxResult(
            purpose=purpose,
            status=TxStatus.FAILED,
            tx_hash=None,
            from_address=from_address,
            to_address=self.gate.address,
            error_name=error_name,
            error=error,
        )

    def send_gate_call(
        self,
        purpose: TxPurpose,
        role_or_key: str,
        fn_name: str,
        args: Sequence[Any],
        *,
        value: int = 0,
        gas: Optional[int] = None,
        sync_clock: bool = True,
    ) -> TxResult:
        """Low-level: signs and sends ``EvidenceGate.<fn_name>(*args)``.

        ``role_or_key`` is a signer role name or a raw private key. ``gas`` skips simulation and estimation
        (the tx is broadcast even if it will revert -- tests use it to exercise the mined-revert path).
        ``sync_clock`` runs the chain's clock hook first (LOCAL_EVM only; a no-op on Monad).
        """
        if role_or_key in self._signers:
            account = self._signers[role_or_key]
        else:
            try:
                account = as_account(role_or_key)
            except Exception:  # noqa: BLE001 - neither a configured role nor a key; never echo the value
                return self._failed(purpose, ZERO_ADDRESS, "MissingSigner", "no private key configured for this signer")
        data = self.gate.encode_abi(fn_name, args=list(args))
        outcome = self.sender.send(account, to=self.gate.address, data=data, value=value, gas=gas, sync_clock=sync_clock)
        return self._to_result(purpose, outcome)

    def _call(self, fn: Any) -> Any:
        return fn.call(block_identifier=self.view_block)

    # -- ChainGateway: reads ---------------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        started = time.monotonic()
        report: dict[str, Any] = {
            "ok": False,
            "mode": self.info.mode.value,
            "network": self.info.network_name,
            "chain_id": self.info.chain_id,
            "evidence_gate": self.info.gate_address,
        }
        try:
            rpc_chain_id = int(self.w3.eth.chain_id)
            block = self.w3.eth.get_block("latest")
            code = self.w3.eth.get_code(self.gate.address)
            report.update(
                {
                    "rpc_chain_id": rpc_chain_id,
                    "chain_id_matches": rpc_chain_id == self.info.chain_id,
                    "block_number": int(block["number"]),
                    "latest_block_timestamp": int(block["timestamp"]),
                    "gate_deployed": len(code) > 0,
                }
            )
            if report["gate_deployed"]:
                report["paused"] = bool(self._call(self.gate.functions.paused()))
            signers: dict[str, Any] = {}
            for role in ("authorizer", "executor", "pauser"):
                address = self.signer_address(role) or (getattr(self.info, role, None) if role != "pauser" else None)
                if not address:
                    continue
                entry: dict[str, Any] = {"address": address, "has_key": role in self._signers}
                entry["balance_wei"] = int(self.w3.eth.get_balance(address))
                if report["gate_deployed"] and role in ("authorizer", "pauser"):
                    role_id = AUTHORIZER_ROLE if role == "authorizer" else PAUSER_ROLE
                    entry["has_role"] = bool(self._call(self.gate.functions.hasRole(role_id, address)))
                signers[role] = entry
            report["signers"] = signers
            report["ok"] = bool(report["chain_id_matches"] and report["gate_deployed"])
        except ChainUnavailable as exc:
            report["error"] = f"ChainUnavailable: {exc}"
        except Exception as exc:  # noqa: BLE001 - health must never raise
            report["error"] = f"{type(exc).__name__}: {exc}"
        provider = self.w3.provider
        if isinstance(provider, FailoverProvider):
            report["rpc"] = {
                "active": provider.active_label,
                "endpoints": provider.labels,
                "failovers": provider.failovers,
                "last_error": provider.last_error,
            }
        report["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
        return report

    def block_timestamp(self) -> int:
        """Timestamp the next transaction will execute with: the pending block where the node supports it
        (LOCAL_EVM: after aligning it with wall-clock time), else the latest block."""
        self._sync()
        if self._pending_supported is not False:
            try:
                block = self.w3.eth.get_block("pending")
                if block is not None and block.get("timestamp") is not None:
                    self._pending_supported = True
                    return int(block["timestamp"])
            except ChainUnavailable:
                raise
            except Exception:  # noqa: BLE001 - "pending" unsupported
                pass
            self._pending_supported = False
        return int(self.w3.eth.get_block("latest")["timestamp"])

    def intent_contract(self, onchain_intent_id: str) -> Optional[str]:
        value = self._call(self.gate.functions.intentContracts(unhex32(onchain_intent_id)))
        return None if bytes(value) == ZERO32 else hexstr(value)

    def get_authorization(self, authorization_id: str) -> OnchainAuthorization:
        """getAuthorization + isAuthorizationActive (LOCAL_EVM: after aligning chain time with the clock,
        so ``active`` reflects wall-clock expiry)."""
        self._sync()
        auth_id = unhex32(authorization_id)
        fields = self._call(self.gate.functions.getAuthorization(auth_id))
        intent_id, contract_hash, evidence_root, action_hash, executor, issued_at, expires_at, consumed = fields
        exists = int(executor, 16) != 0 if isinstance(executor, str) else bool(executor)
        if not exists:
            return OnchainAuthorization(authorization_id=hexstr(auth_id), exists=False)
        active = bool(self._call(self.gate.functions.isAuthorizationActive(auth_id)))
        return OnchainAuthorization(
            authorization_id=hexstr(auth_id),
            exists=True,
            intent_id=hexstr(intent_id),
            contract_hash=hexstr(contract_hash),
            evidence_root=hexstr(evidence_root),
            action_hash=hexstr(action_hash),
            executor=to_checksum_address(executor),
            issued_at=int(issued_at),
            expires_at=int(expires_at),
            consumed=bool(consumed),
            active=active,
        )

    def compute_action_hash(self, action: CanonicalAction) -> str:
        return hexstr(self._call(self.gate.functions.computeActionHash(action_tuple(action))))

    def receipt_commitment(self, authorization_id: str) -> Optional[str]:
        value = self._call(self.gate.functions.receiptHashes(unhex32(authorization_id)))
        return None if bytes(value) == ZERO32 else hexstr(value)

    def token_balance(self, token: str, holder: str) -> int:
        erc20 = self.w3.eth.contract(address=to_checksum_address(token), abi=self._erc20_abi)
        return int(self._call(erc20.functions.balanceOf(to_checksum_address(holder))))

    def is_paused(self) -> bool:
        return bool(self._call(self.gate.functions.paused()))

    def has_role(self, role: str, address: str) -> bool:
        role_id = {"authorizer": AUTHORIZER_ROLE, "pauser": PAUSER_ROLE, "admin": ZERO32}[role]
        return bool(self._call(self.gate.functions.hasRole(role_id, to_checksum_address(address))))

    def explorer_tx_url(self, tx_hash: Optional[str]) -> Optional[str]:
        if not tx_hash or self.info.mode == ChainMode.LOCAL_EVM or not self.info.explorer_url:
            return None
        return f"{self.info.explorer_url.rstrip('/')}/tx/{tx_hash}"

    def explorer_address_url(self, address: Optional[str]) -> Optional[str]:
        if not address or self.info.mode == ChainMode.LOCAL_EVM or not self.info.explorer_url:
            return None
        return f"{self.info.explorer_url.rstrip('/')}/address/{address}"

    def find_transaction(self, tx_hash: str, purpose: Optional[TxPurpose] = None) -> Optional[TxResult]:
        """Looks a transaction up (reconciliation after SUBMITTED). None if the node does not know it."""
        tx_hash = hexstr(tx_hash)
        try:
            tx = self.w3.eth.get_transaction(tx_hash)
        except (TransactionNotFound, BlockNotFound):
            tx = None
        try:
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
        except (TransactionNotFound, BlockNotFound):
            receipt = None
        if tx is None and receipt is None:
            return None
        data = bytes(tx["input"]) if tx is not None else b""
        purpose = purpose or self._selectors.get(data[:4], TxPurpose.DEPLOYMENT)
        from_address = to_checksum_address((tx or receipt)["from"])
        to_raw = (tx or receipt).get("to")
        to_address = to_checksum_address(to_raw) if to_raw else None
        if receipt is None:
            return TxResult(
                purpose=purpose,
                status=TxStatus.SUBMITTED,
                tx_hash=tx_hash,
                from_address=from_address,
                to_address=to_address,
                explorer_url=self.explorer_tx_url(tx_hash),
            )
        outcome = SendOutcome(
            status=TxStatus.CONFIRMED if int(receipt.get("status", 0)) == 1 else TxStatus.REVERTED,
            from_address=from_address,
            to_address=to_address,
            tx_hash=tx_hash,
            receipt=receipt,
        )
        if outcome.status == TxStatus.REVERTED and tx is not None:
            call: dict[str, Any] = {"from": from_address, "data": hexstr(data), "value": int(tx["value"]), "gas": int(tx["gas"])}
            if to_address:
                call["to"] = to_address
            name, args = self.sender.replay_revert(call, receipt.get("blockNumber"))
            outcome.error_name = name
            outcome.error = "transaction reverted onchain: " + describe_revert(name, args)
        return self._to_result(purpose, outcome)

    # -- ChainGateway: writes --------------------------------------------------------------------------

    def register_intent(self, onchain_intent_id: str, contract_hash: str) -> TxResult:
        """Binds an intent to its contract hash. Idempotent: when the same pair is already registered,
        no transaction is sent and ``TxResult(status=CONFIRMED, tx_hash=None, events=[])`` is returned
        (a different hash for the same intent is rejected by the contract: IntentAlreadyRegistered)."""
        purpose = TxPurpose.INTENT_REGISTER
        authorizer = self.signer_address("authorizer") or self.info.authorizer
        try:
            existing = self.intent_contract(onchain_intent_id)
        except ChainUnavailable as exc:
            return self._failed(purpose, authorizer, "ChainUnavailable", f"not sent: {exc}")
        except Exception as exc:  # noqa: BLE001 - fail closed, nothing sent
            return self._failed(purpose, authorizer, type(exc).__name__, f"not sent: {exc}")
        if existing is not None and existing.lower() == contract_hash.lower():
            return TxResult(
                purpose=purpose,
                status=TxStatus.CONFIRMED,
                tx_hash=None,
                from_address=authorizer,
                to_address=self.gate.address,
                error_name=None,
                error=None,
                events=[],
            )
        return self.send_gate_call(purpose, "authorizer", "registerIntent", [unhex32(onchain_intent_id), unhex32(contract_hash)])

    def authorize(self, authorization: Authorization) -> TxResult:
        args = [
            unhex32(authorization.authorization_id),
            unhex32(authorization.onchain_intent_id),
            unhex32(authorization.contract_hash),
            unhex32(authorization.evidence_root),
            unhex32(authorization.action_hash),
            to_checksum_address(authorization.executor),
            math.floor(authorization.issued_at.timestamp()),
            math.floor(authorization.expires_at.timestamp()),
            unhex32(authorization.nonce),
        ]
        return self.send_gate_call(TxPurpose.AUTHORIZATION, "authorizer", "authorize", args)

    def execute(self, authorization_id: str, action: CanonicalAction, *, executor_key: Optional[str] = None) -> TxResult:
        signer = executor_key or "executor"
        return self.send_gate_call(
            TxPurpose.PROTECTED_EXECUTION,
            signer,
            "executeAuthorized",
            [unhex32(authorization_id), action_tuple(action)],
            value=int(action.value),
        )

    def record_receipt(self, authorization_id: str, receipt_hash: str) -> TxResult:
        return self.send_gate_call(
            TxPurpose.RECEIPT_COMMITMENT, "authorizer", "recordReceipt", [unhex32(authorization_id), unhex32(receipt_hash)]
        )

    # -- admin (not part of the protocol; TxPurpose has no ADMIN member, DEPLOYMENT is used) ----------

    def pause(self) -> TxResult:
        return self.send_gate_call(TxPurpose.DEPLOYMENT, "pauser", "pause", [])

    def unpause(self) -> TxResult:
        return self.send_gate_call(TxPurpose.DEPLOYMENT, "pauser", "unpause", [])


# =============================================================================================
# Helpers
# =============================================================================================


def action_tuple(action: CanonicalAction) -> tuple:
    """EvidenceGate.Action struct, in declaration order."""
    return (
        to_checksum_address(action.target),
        int(action.value),
        unhex(action.data),
        to_checksum_address(action.asset_in),
        int(action.max_amount_in),
        to_checksum_address(action.asset_out),
        int(action.min_amount_out),
        to_checksum_address(action.recipient),
        int(action.deadline),
        unhex32(action.nonce),
    )


def _has_event(contract: Any, name: str) -> bool:
    return any(item.get("type") == "event" and item.get("name") == name for item in contract.abi)


def _fn_selector(contract: Any, fn_name: str) -> bytes:
    for item in contract.abi:
        if item.get("type") == "function" and item.get("name") == fn_name:
            signature = f"{fn_name}({','.join(abi_type(i) for i in item['inputs'])})"
            return keccak(text=signature)[:4]
    raise KeyError(fn_name)


__all__ = [
    "ConfigurationError",
    "FailoverProvider",
    "SendOutcome",
    "TransactionSender",
    "Web3Gateway",
    "action_tuple",
    "http_providers",
    "is_connection_error",
    "redact_url",
    "signer_lock",
]
