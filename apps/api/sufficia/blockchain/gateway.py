"""Chain gateway interface: the only way the backend touches a blockchain.

Implementations (``sufficia.blockchain.web3_gateway``):
  * Web3Gateway over an HTTP provider  -> MONAD_TESTNET / MONAD_MAINNET (primary + fallback RPC)
  * Web3Gateway over an in-process py-evm chain -> LOCAL_EVM (real EvidenceGate bytecode, dev keys)

The backend is a convenience layer; the contract is the enforcement boundary (Document 04 §67).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol, runtime_checkable

from ..domain.enums import ChainMode, TxPurpose, TxStatus
from ..domain.models import Authorization, CanonicalAction


class ChainUnavailable(RuntimeError):
    """No RPC endpoint could be reached. Never converted into a success (fail closed)."""


@dataclass
class TxResult:
    purpose: TxPurpose
    status: TxStatus  # CONFIRMED | REVERTED | FAILED (never submitted)
    tx_hash: Optional[str]
    from_address: str
    to_address: Optional[str]
    block_number: Optional[int] = None
    block_timestamp: Optional[int] = None
    gas_used: Optional[int] = None
    error_name: Optional[str] = None  # decoded custom error, e.g. "ActionHashMismatch"
    error: Optional[str] = None
    events: list[dict[str, Any]] = field(default_factory=list)  # decoded EvidenceGate events
    submitted_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    explorer_url: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == TxStatus.CONFIRMED


@dataclass
class OnchainAuthorization:
    authorization_id: str
    exists: bool
    intent_id: Optional[str] = None
    contract_hash: Optional[str] = None
    evidence_root: Optional[str] = None
    action_hash: Optional[str] = None
    executor: Optional[str] = None
    issued_at: Optional[int] = None
    expires_at: Optional[int] = None
    consumed: bool = False
    active: bool = False


@dataclass
class ChainInfo:
    mode: ChainMode
    chain_id: int
    network_name: str
    gate_address: str
    usdc_address: Optional[str]
    token_x_address: Optional[str]
    router_address: Optional[str]
    explorer_url: Optional[str]
    authorizer: str
    executor: str
    deployment: dict[str, Any] = field(default_factory=dict)
    usdc_decimals: int = 6
    token_x_decimals: int = 18

    def public(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "chain_id": self.chain_id,
            "network": self.network_name,
            "evidence_gate": self.gate_address,
            "usdc": self.usdc_address,
            "token_x": self.token_x_address,
            "router": self.router_address,
            "explorer_url": self.explorer_url,
            "authorizer": self.authorizer,
            "executor": self.executor,
            "deployment": self.deployment,
        }


@runtime_checkable
class ChainGateway(Protocol):
    info: ChainInfo

    def health(self) -> dict[str, Any]: ...

    def block_timestamp(self) -> int:
        """Timestamp the *next* transaction will execute with (pending block where available)."""
        ...

    def register_intent(self, onchain_intent_id: str, contract_hash: str) -> TxResult: ...

    def intent_contract(self, onchain_intent_id: str) -> Optional[str]: ...

    def authorize(self, authorization: Authorization) -> TxResult: ...

    def get_authorization(self, authorization_id: str) -> OnchainAuthorization: ...

    def compute_action_hash(self, action: CanonicalAction) -> str:
        """EvidenceGate.computeActionHash via eth_call (parity check with the backend)."""
        ...

    def execute(self, authorization_id: str, action: CanonicalAction, *, executor_key: Optional[str] = None) -> TxResult: ...

    def record_receipt(self, authorization_id: str, receipt_hash: str) -> TxResult: ...

    def receipt_commitment(self, authorization_id: str) -> Optional[str]: ...

    def token_balance(self, token: str, holder: str) -> int: ...

    def is_paused(self) -> bool: ...

    def explorer_tx_url(self, tx_hash: Optional[str]) -> Optional[str]: ...

    def find_transaction(self, tx_hash: str) -> Optional[TxResult]:
        """Reconciliation after a timeout: look a transaction up instead of resubmitting blindly."""
        ...
