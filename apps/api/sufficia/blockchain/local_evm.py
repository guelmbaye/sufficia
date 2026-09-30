"""LOCAL_EVM: an in-process py-evm chain running the real EvidenceGate bytecode.

This is NOT Monad (``network_name = "local-evm (py-evm, not Monad)"``, no explorer links). It exists so the
full authorize -> execute -> receipt path runs offline with the exact same gateway code as Monad:
transactions are signed locally with the dev keys (``LOCAL_DEV_KEYS`` unless overridden) and broadcast with
``eth_sendRawTransaction``; the demo suite is deployed by :func:`sufficia.blockchain.deploy.deploy_suite`.

Time model
----------
eth-tester auto-mines one block per transaction and a transaction executes with the PENDING block's
timestamp. The backend computes authorization expiries from wall-clock time, so chain time must track it.

Stock py-evm stamps each new block ``max(parent + 1, wall clock)`` (Ethereum's strictly-increasing rule), so
an idle chain lags until the next block and a burst of N transactions in under N seconds runs up to N seconds
AHEAD of wall clock -- enough to eat a 30-60 s authority window during a busy demo or a test run.

The local chain therefore runs a Prague VM that also accepts a block with the SAME timestamp as its parent
(:class:`RelaxedTimestampPragueVM`; EVM execution is unchanged, and no SUFFICIA contract depends on
strictly increasing timestamps), and :meth:`LocalEvmChain.sync_clock` pins the pending block to::

    max(parent_timestamp, int(time.time()), advance_floor)

before EVERY transaction the gateway sends (the gateway's ``clock_sync`` hook), in ``block_timestamp()`` and
before ``get_authorization()``. Chain time never goes backwards (a mined block is never below its parent) and
never runs ahead of wall clock unless a test called :meth:`LocalEvmChain.advance_time`, which raises the
floor (irreversible: the chain then stays that far ahead until wall clock catches up).

If the relaxed VM cannot be installed (py-evm API change), the chain falls back to the stock VM and
``sync_clock`` uses ``time_travel(max(pending_ts, now))`` (never backwards; bursts can then drift ahead).

Thread safety: FastAPI runs sync endpoints in a thread pool, so every provider request, clock sync and time
travel goes through one process-wide re-entrant lock around the tester, and the transaction pipeline holds
one lock per signer (lock order everywhere: signer lock -> tester lock).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Mapping, Optional

from eth_account import Account
from web3 import EthereumTesterProvider, Web3

from ..domain.enums import ChainMode
from .deploy import LOCAL_NETWORK_NAME, Deployment, chain_info_from_deployment, deploy_suite
from .web3_gateway import ConfigurationError, Web3Gateway

log = logging.getLogger(__name__)

#: Native balance given to every local signer (gas money; local chain only).
LOCAL_SIGNER_FUNDING_WEI = 1_000 * 10**18
DEFAULT_GENESIS_BACKDATE = 60

#: One lock for every in-process tester (py-evm is not thread-safe; cheap to share).
TESTER_LOCK = threading.RLock()


def _require_eth_tester() -> tuple[Any, Any]:
    try:
        from eth_tester import EthereumTester, PyEVMBackend
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ConfigurationError(
            "CHAIN_MODE=LOCAL_EVM needs eth-tester with py-evm: pip install 'sufficia[local-evm]'"
        ) from exc
    return EthereumTester, PyEVMBackend


def _relaxed_vm_class() -> Optional[type]:
    """PragueVM that accepts ``header.timestamp == parent.timestamp`` (all other header rules unchanged)."""
    try:
        from eth.vm.forks import PragueVM
    except Exception:  # noqa: BLE001 - py-evm layout changed: strict fallback
        return None

    class RelaxedTimestampPragueVM(PragueVM):  # type: ignore[misc, valid-type]
        @classmethod
        def validate_header(cls, header: Any, parent_header: Any) -> None:
            if parent_header is not None and header.timestamp == parent_header.timestamp:
                # Validate against a copy of the parent one second earlier: every other rule still applies.
                parent_header = parent_header.copy(timestamp=parent_header.timestamp - 1)
            super().validate_header(header, parent_header)

    return RelaxedTimestampPragueVM


RelaxedTimestampPragueVM = _relaxed_vm_class()


class LockedEthereumTesterProvider(EthereumTesterProvider):
    """EthereumTesterProvider whose requests are serialized by a shared re-entrant lock."""

    def __init__(self, ethereum_tester: Any, lock: threading.RLock) -> None:
        super().__init__(ethereum_tester)
        self._lock = lock

    def make_request(self, method: Any, params: Any) -> Any:
        with self._lock:
            return super().make_request(method, params)


class LocalEvmChain:
    """A fresh in-memory chain + demo deployment + a :class:`Web3Gateway` (``ChainMode.LOCAL_EVM``).

    ``keys`` overrides entries of ``LOCAL_DEV_KEYS`` (roles: deployer, authorizer, executor, pauser,
    evidence_payer). Amount arguments are base units and default to the deploy_suite defaults.
    """

    def __init__(
        self,
        keys: Optional[Mapping[str, Optional[str]]] = None,
        *,
        deploy: bool = True,
        tx_timeout: float = 30.0,
        genesis_backdate: int = DEFAULT_GENESIS_BACKDATE,
        usdc_decimals: int = 6,
        gate_usdc_funding: Optional[int] = None,
        router_tokenx_reserve: Optional[int] = None,
        rate_e18: Optional[int] = None,
        relaxed_timestamps: bool = True,
    ) -> None:
        from ..config import LOCAL_DEV_KEYS

        EthereumTester, PyEVMBackend = _require_eth_tester()
        merged = dict(LOCAL_DEV_KEYS)
        for role, key in (keys or {}).items():
            if key:
                merged[role] = key
        self.keys: dict[str, str] = merged
        self.accounts = {role: Account.from_key(key) for role, key in merged.items()}
        self.lock = TESTER_LOCK
        self.tx_timeout = tx_timeout
        self._floor = 0

        with self.lock:
            genesis = PyEVMBackend.generate_genesis_params(
                overrides={"timestamp": int(time.time()) - max(0, int(genesis_backdate))}
            )
            backend = None
            self.relaxed_timestamps = False
            if relaxed_timestamps and RelaxedTimestampPragueVM is not None:
                try:
                    backend = PyEVMBackend(genesis_parameters=genesis, vm_configuration=((0, RelaxedTimestampPragueVM),))
                    self.relaxed_timestamps = True
                except Exception as exc:  # noqa: BLE001 - strict fallback
                    log.warning("relaxed-timestamp VM unavailable (%s); using strict py-evm timestamps", exc)
            if backend is None:
                backend = PyEVMBackend(genesis_parameters=genesis)
            self.tester = EthereumTester(backend)
            self.w3 = Web3(LockedEthereumTesterProvider(self.tester, self.lock))
            self.chain_id = int(self.w3.eth.chain_id)
            self._fund_signers()

        self.deployment: Optional[Deployment] = None
        self.gateway: Optional[Web3Gateway] = None
        if deploy:
            kwargs: dict[str, Any] = {}
            if rate_e18 is not None:
                kwargs["rate_e18"] = rate_e18
            # Deliberately NOT under self.lock: the transaction pipeline takes the signer lock first and the
            # tester lock per request, and that lock order must hold everywhere (no deadlock). Nobody else
            # can reach this chain before __init__ returns.
            self.deployment = deploy_suite(
                self.w3,
                deployer_key=self.keys["deployer"],
                authorizer=self.accounts["authorizer"].address,
                pauser=self.accounts["pauser"].address,
                executor=self.accounts["executor"].address,
                usdc_decimals=usdc_decimals,
                gate_usdc_funding=gate_usdc_funding,
                router_tokenx_reserve=router_tokenx_reserve,
                network=LOCAL_NETWORK_NAME,
                tx_timeout=tx_timeout,
                poll_interval=0.01,
                before_tx=self.bootstrap_clock,
                **kwargs,
            )
            info = chain_info_from_deployment(
                self.deployment,
                mode=ChainMode.LOCAL_EVM,
                authorizer=self.accounts["authorizer"].address,
                executor=self.accounts["executor"].address,
                network_name=LOCAL_NETWORK_NAME,
            )
            self.gateway = Web3Gateway(
                w3=self.w3,
                info=info,
                signer_keys={role: self.keys[role] for role in ("authorizer", "executor", "pauser", "deployer")},
                tx_timeout=tx_timeout,
                poll_interval=0.01,
                clock_sync=self.sync_clock,
                view_block="pending",
                simulate_block="pending",
            )
        self.sync_clock()

    # -- bootstrap ---------------------------------------------------------------------------------

    def _fund_signers(self) -> None:
        """Funds every signer address from the tester's prefunded (unlocked) accounts."""
        source = self.w3.eth.accounts[0]
        funded: set[str] = set()
        for account in self.accounts.values():
            if account.address in funded:
                continue
            funded.add(account.address)
            self.bootstrap_clock()
            tx_hash = self.w3.eth.send_transaction(
                {"from": source, "to": account.address, "value": LOCAL_SIGNER_FUNDING_WEI, "gas": 21_000}
            )
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=self.tx_timeout, poll_latency=0.01)
            if receipt["status"] != 1:  # pragma: no cover - defensive
                raise RuntimeError(f"funding {account.address} failed")

    def bootstrap_clock(self) -> None:
        """Before each bootstrap / deploy-script transaction. Relaxed mode: same as sync_clock. Strict mode:
        pin the block to parent + 1 (from the backdated genesis) so start-up never runs ahead of wall clock."""
        if self.relaxed_timestamps:
            self.sync_clock()
            return
        with self.lock:
            try:
                chain = self.tester.backend.chain
                target = chain.get_canonical_head().timestamp + 1
                if target < chain.header.timestamp and target <= int(time.time()) and self._pending_is_empty():
                    chain.header = chain.header.copy(timestamp=target)
            except Exception as exc:  # noqa: BLE001 - cosmetic; the next sync realigns anyway
                log.debug("bootstrap timestamp pin skipped: %s", exc)

    # -- time ------------------------------------------------------------------------------------

    def _pending_is_empty(self) -> bool:
        from eth.constants import BLANK_ROOT_HASH

        return self.tester.backend.chain.header.transaction_root == BLANK_ROOT_HASH

    def pending_timestamp(self) -> int:
        with self.lock:
            return int(self.tester.get_block_by_number("pending")["timestamp"])

    def latest_timestamp(self) -> int:
        with self.lock:
            return int(self.tester.get_block_by_number("latest")["timestamp"])

    def sync_clock(self) -> int:
        """Aligns the pending block with wall-clock time (see the module docstring). Returns its timestamp."""
        with self.lock:
            now = int(time.time())
            if self.relaxed_timestamps:
                chain = self.tester.backend.chain
                parent = int(chain.get_canonical_head().timestamp)
                target = max(parent, now, self._floor)
                # Never re-stamp a pending block that already holds transactions (auto-mining disabled):
                # their execution already used its timestamp.
                if int(chain.header.timestamp) != target and self._pending_is_empty():
                    chain.header = chain.header.copy(timestamp=target)
                return int(chain.header.timestamp)
            pending = self.pending_timestamp()
            target = max(pending, now, self._floor)
            if target > pending:
                self.tester.time_travel(target)
            return target

    def advance_time(self, seconds: int) -> int:
        """Tests: moves chain time ``seconds`` beyond wall clock (irreversible). Returns the new pending
        timestamp; the next transaction executes with it."""
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        with self.lock:
            base = self.sync_clock()
            self._floor = base + int(seconds)
            return self.sync_clock()

    def mine(self, blocks: int = 1) -> None:
        with self.lock:
            self.tester.mine_blocks(blocks)

    # -- convenience -------------------------------------------------------------------------------

    def address(self, role: str) -> str:
        return self.accounts[role].address

    def require_gateway(self) -> Web3Gateway:
        if self.gateway is None:
            raise ConfigurationError("this LocalEvmChain was created with deploy=False")
        return self.gateway


__all__ = [
    "LOCAL_NETWORK_NAME",
    "LocalEvmChain",
    "LockedEthereumTesterProvider",
    "RelaxedTimestampPragueVM",
    "TESTER_LOCK",
]
