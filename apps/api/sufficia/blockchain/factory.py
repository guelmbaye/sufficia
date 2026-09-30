"""Builds the ChainGateway for the configured ``CHAIN_MODE``.

  LOCAL_EVM                     -> the process-wide :class:`LocalEvmChain` (created on first use; dev keys
                                   unless keys are set in the environment). ``reset_local_chain()`` for tests.
  MONAD_TESTNET / MONAD_MAINNET -> :class:`Web3Gateway` over HTTP providers ``[MONAD_RPC_URL,
                                   MONAD_RPC_FALLBACK_URL]`` with addresses from ``DEPLOYMENT_FILE`` and/or
                                   explicit settings, signing with ``AUTHORIZER_PRIVATE_KEY`` /
                                   ``EXECUTOR_PRIVATE_KEY`` (``PAUSER_PRIVATE_KEY`` optional).
  DISABLED                      -> ``None``.

A Monad mode with missing keys or addresses raises :class:`ConfigurationError`; it never falls back to the
local chain (that would silently turn "executed on Monad" into "executed on a simulator").
Building a Monad gateway performs no network call (connectivity is reported by ``gateway.health()``).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

from eth_account import Account

from ..domain.enums import ChainMode
from .deploy import chain_info_from_settings, load_deployment
from .gateway import ChainGateway
from .web3_gateway import ConfigurationError, Web3Gateway

_LOCAL_CHAIN: Any = None
_LOCAL_LOCK = threading.Lock()


def get_local_chain(settings: Any = None) -> Any:
    """The process-wide LocalEvmChain (created on first call)."""
    global _LOCAL_CHAIN
    with _LOCAL_LOCK:
        if _LOCAL_CHAIN is None:
            from .local_evm import LocalEvmChain

            keys: dict[str, Optional[str]] = {}
            timeout = 30.0
            if settings is not None:
                keys = {
                    role: settings.key(role)
                    for role in ("deployer", "authorizer", "executor", "pauser", "evidence_payer")
                }
                timeout = float(settings.tx_confirmation_timeout_seconds)
            _LOCAL_CHAIN = LocalEvmChain(keys, tx_timeout=timeout)
        return _LOCAL_CHAIN


def reset_local_chain() -> None:
    """Drops the process-wide local chain (tests); the next build_gateway() creates a fresh one."""
    global _LOCAL_CHAIN
    with _LOCAL_LOCK:
        _LOCAL_CHAIN = None


def _address_of(key: str, role: str) -> str:
    try:
        return Account.from_key(key).address
    except Exception as exc:  # noqa: BLE001 - never echo key material
        raise ConfigurationError(f"{role.upper()}_PRIVATE_KEY is not a valid private key ({type(exc).__name__})") from None


def _resolve_deployment_path(value: str) -> Path:
    from ..config import API_ROOT, REPO_ROOT

    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    for base in (Path.cwd(), REPO_ROOT, API_ROOT):
        candidate = base / path
        if candidate.is_file():
            return candidate
    return REPO_ROOT / path


def build_monad_gateway(settings: Any) -> Web3Gateway:
    mode = settings.chain_mode
    missing = [
        f"{role.upper()}_PRIVATE_KEY" for role in ("authorizer", "executor") if not settings.key(role)
    ]
    if not settings.monad_rpc_url:
        missing.append("MONAD_RPC_URL")
    if missing:
        raise ConfigurationError(f"CHAIN_MODE={mode.value} requires {', '.join(missing)}")
    authorizer_key = settings.key("authorizer")
    executor_key = settings.key("executor")
    authorizer = _address_of(authorizer_key, "authorizer")
    executor = _address_of(executor_key, "executor")

    deployment = None
    if settings.deployment_file:
        deployment = load_deployment(_resolve_deployment_path(settings.deployment_file))
    info = chain_info_from_settings(settings, deployment, authorizer=authorizer, executor=executor)

    keys = {"authorizer": authorizer_key, "executor": executor_key}
    pauser_key = settings.key("pauser")
    if pauser_key:
        _address_of(pauser_key, "pauser")
        keys["pauser"] = pauser_key
    return Web3Gateway.from_rpc_urls(
        [settings.monad_rpc_url, settings.monad_rpc_fallback_url],
        info=info,
        signer_keys=keys,
        request_timeout=max(1.0, float(getattr(settings, "http_timeout_seconds", 10.0) or 10.0)),
        tx_timeout=float(settings.tx_confirmation_timeout_seconds),
        poll_interval=0.5,
    )


def build_gateway(settings: Any = None) -> Optional[ChainGateway]:
    if settings is None:
        from ..config import get_settings

        settings = get_settings()
    mode = settings.chain_mode
    if mode == ChainMode.DISABLED:
        return None
    if mode == ChainMode.LOCAL_EVM:
        return get_local_chain(settings).require_gateway()
    if mode in (ChainMode.MONAD_TESTNET, ChainMode.MONAD_MAINNET):
        return build_monad_gateway(settings)
    raise ConfigurationError(f"unsupported CHAIN_MODE {mode!r}")


__all__ = ["ConfigurationError", "build_gateway", "build_monad_gateway", "get_local_chain", "reset_local_chain"]
