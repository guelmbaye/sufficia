"""Execution adapters: authorized Evidence Contract -> exact EvidenceGate action."""

from .adapters import (
    ADAPTERS,
    AdapterError,
    DemoSwapAdapter,
    ExecutionAdapter,
    TransferAdapter,
    action_deadline,
    action_nonce,
    get_adapter,
    prepare_action,
    price_observations,
    verify_spend,
)

__all__ = [
    "ADAPTERS",
    "AdapterError",
    "DemoSwapAdapter",
    "ExecutionAdapter",
    "TransferAdapter",
    "action_deadline",
    "action_nonce",
    "get_adapter",
    "prepare_action",
    "price_observations",
    "verify_spend",
]
