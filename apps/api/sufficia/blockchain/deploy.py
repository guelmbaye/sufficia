"""Python deployer for the SUFFICIA demo suite (the backend does not depend on Foundry).

``deploy_suite`` deploys, with locally signed transactions (same pipeline as the gateway):

  1. DemoERC20 "Demo USD Coin" / dUSDC (6 decimals)      -- skipped when ``existing_usdc`` is given
  2. DemoERC20 "Demo Token X" / TOKENX (18 decimals)
  3. DemoSwapRouter(owner=deployer) + setRate(usdc, tokenx, rate_e18)   (1 USDC = 2.336 TOKEN-X)
  4. TOKEN-X reserve minted to the router
  5. EvidenceGate(admin=deployer, authorizer, pauser, targets=[router, usdc])
     (usdc is allowlisted so the ``transfer`` execution adapter can call ``usdc.transfer``)
  6. demo dUSDC minted to the gate (its protected treasury)   -- skipped with ``existing_usdc``
     (an external USDC cannot be minted: fund the gate by transfer)

The result is a :class:`Deployment` (JSON-serializable; written to ``deployments/<network>.json`` by
``scripts/deploy.py`` and read back by the gateway factory for Monad modes).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from eth_utils import is_address, to_checksum_address
from web3 import Web3

from ..domain.enums import ChainMode
from .artifacts import contract_abi, describe_revert, encode_constructor
from .gateway import ChainInfo
from .web3_gateway import ConfigurationError, SendOutcome, TransactionSender, as_account

log = logging.getLogger(__name__)

USDC_NAME = "Demo USD Coin"
USDC_SYMBOL = "dUSDC"
TOKEN_X_NAME = "Demo Token X"
TOKEN_X_SYMBOL = "TOKENX"
TOKEN_X_DECIMALS = 18
DEFAULT_RATE_E18 = 2_336_000_000_000_000_000  # 1 USDC = 2.336 TOKEN-X (TOKEN-X ~= $0.428)

MONAD_TESTNET_CHAIN_ID = 10143
MONAD_MAINNET_CHAIN_ID = 143
MONAD_TESTNET_RPC = "https://testnet-rpc.monad.xyz"
MONAD_MAINNET_RPC = "https://rpc.monad.xyz"
MONAD_TESTNET_EXPLORER = "https://testnet.monadvision.com"
MONAD_MAINNET_EXPLORER = "https://monadvision.com"
LOCAL_NETWORK_NAME = "local-evm (py-evm, not Monad)"

NETWORK_NAMES = {
    ChainMode.MONAD_TESTNET: "monad-testnet",
    ChainMode.MONAD_MAINNET: "monad-mainnet",
    ChainMode.LOCAL_EVM: LOCAL_NETWORK_NAME,
}

# Integers above 2**53 are written as decimal strings so JavaScript consumers keep full precision.
_BIG_INT_FIELDS = ("rate_e18", "gate_usdc_funding", "router_tokenx_reserve")


class DeploymentError(RuntimeError):
    def __init__(self, step: str, message: str, *, tx_hash: Optional[str] = None, partial: Optional[dict] = None):
        super().__init__(f"{step}: {message}" + (f" (tx {tx_hash})" if tx_hash else ""))
        self.step = step
        self.tx_hash = tx_hash
        self.partial = partial or {}


@dataclass
class Deployment:
    chain_id: int
    gate: str
    usdc: str
    token_x: str
    router: str
    deployer: str
    authorizer: str
    pauser: str
    executor: str
    txs: dict[str, str] = field(default_factory=dict)
    block_number: int = 0
    deployed_at: str = ""
    network: Optional[str] = None
    usdc_decimals: int = 6
    token_x_decimals: int = TOKEN_X_DECIMALS
    rate_e18: int = DEFAULT_RATE_E18
    gate_usdc_funding: int = 0
    router_tokenx_reserve: int = 0
    usdc_is_demo: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in _BIG_INT_FIELDS:
            data[key] = str(data[key])
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Deployment":
        required = ("chain_id", "gate", "usdc", "token_x", "router", "deployer", "authorizer", "pauser", "executor")
        missing = [k for k in required if not data.get(k)]
        if missing:
            raise ConfigurationError(f"deployment is missing: {', '.join(missing)}")
        known = set(cls.__dataclass_fields__)
        values = {k: v for k, v in data.items() if k in known}
        for key in ("gate", "usdc", "token_x", "router", "deployer", "authorizer", "pauser", "executor"):
            if not is_address(values[key]):
                raise ConfigurationError(f"deployment field '{key}' is not an address: {values[key]!r}")
            values[key] = to_checksum_address(values[key])
        for key in ("chain_id", "block_number", "usdc_decimals", "token_x_decimals") + _BIG_INT_FIELDS:
            if key in values and values[key] is not None:
                values[key] = int(values[key])
        values["txs"] = dict(values.get("txs") or {})
        return cls(**values)

    @classmethod
    def from_json(cls, text: str | bytes | Mapping[str, Any]) -> "Deployment":
        if isinstance(text, Mapping):
            return cls.from_dict(text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"deployment JSON is invalid: {exc}") from None
        if not isinstance(data, dict):
            raise ConfigurationError("deployment JSON must be an object")
        return cls.from_dict(data)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(), encoding="utf-8")
        return target


def load_deployment(path: str | Path) -> Deployment:
    target = Path(path)
    if not target.is_file():
        raise ConfigurationError(f"deployment file not found: {target} (run scripts/deploy.py first)")
    return Deployment.from_json(target.read_text(encoding="utf-8"))


# =============================================================================================
# Deployment
# =============================================================================================


def _address(value: str, name: str) -> str:
    if not value or not is_address(value):
        raise ConfigurationError(f"{name} must be an EVM address, got {value!r}")
    return to_checksum_address(value)


def deploy_suite(
    w3: Web3,
    *,
    deployer_key: str,
    authorizer: str,
    pauser: str,
    executor: str,
    usdc_decimals: int = 6,
    rate_e18: int = DEFAULT_RATE_E18,
    gate_usdc_funding: Optional[int] = None,
    router_tokenx_reserve: Optional[int] = None,
    existing_usdc: str | None = None,
    network: Optional[str] = None,
    tx_timeout: float = 120.0,
    poll_interval: float = 0.5,
    before_tx: Optional[Callable[[], Any]] = None,
    on_step: Optional[Callable[[str, SendOutcome], Any]] = None,
) -> Deployment:
    """Deploys the demo suite and returns the :class:`Deployment`. Raises :class:`DeploymentError`.

    Amounts are in base units: ``gate_usdc_funding`` defaults to 1_000 USDC, ``router_tokenx_reserve`` to
    1_000_000 TOKEN-X. ``before_tx`` runs before every transaction (the local chain uses it to keep the
    bootstrap blocks from pushing chain time ahead of wall-clock time). ``on_step(name, outcome)`` is
    called after each confirmed transaction (progress output for the CLI).
    """
    deployer = as_account(deployer_key)
    authorizer = _address(authorizer, "authorizer")
    pauser = _address(pauser, "pauser")
    executor = _address(executor, "executor")
    chain_id = int(w3.eth.chain_id)
    sender = TransactionSender(w3, chain_id, tx_timeout=tx_timeout, poll_interval=poll_interval, clock_sync=before_tx)
    txs: dict[str, str] = {}
    last_block = 0

    def run(step: str, *, to: Optional[str], data: str) -> SendOutcome:
        nonlocal last_block
        outcome = sender.send(deployer, to=to, data=data, sync_clock=before_tx is not None)
        if outcome.tx_hash:
            txs[step] = outcome.tx_hash
        if not outcome.ok:
            detail = outcome.error or describe_revert(outcome.error_name, outcome.error_args)
            raise DeploymentError(step, f"{outcome.status.value}: {detail}", tx_hash=outcome.tx_hash, partial=dict(txs))
        last_block = max(last_block, int(outcome.receipt["blockNumber"]))
        if on_step is not None:
            on_step(step, outcome)
        return outcome

    def deploy(step: str, name: str, *args: Any) -> str:
        outcome = run(step, to=None, data=encode_constructor(name, args))
        address = outcome.contract_address
        if not address:
            raise DeploymentError(step, "receipt has no contractAddress", tx_hash=outcome.tx_hash, partial=dict(txs))
        return address

    def call(step: str, address: str, name: str, fn: str, *args: Any) -> None:
        contract = w3.eth.contract(address=address, abi=contract_abi(name))
        run(step, to=address, data=contract.encode_abi(fn, args=list(args)))

    usdc_is_demo = existing_usdc is None
    if existing_usdc is not None:
        usdc = _address(existing_usdc, "existing_usdc")
        if len(w3.eth.get_code(usdc)) == 0:
            raise DeploymentError("usdc", f"no contract at {usdc} on chain {chain_id}")
        token = w3.eth.contract(address=usdc, abi=contract_abi("DemoERC20"))
        usdc_decimals = int(token.functions.decimals().call())
    else:
        usdc = deploy("deploy_usdc", "DemoERC20", USDC_NAME, USDC_SYMBOL, int(usdc_decimals), deployer.address)

    if gate_usdc_funding is None:
        gate_usdc_funding = 1_000 * 10**usdc_decimals
    if router_tokenx_reserve is None:
        router_tokenx_reserve = 1_000_000 * 10**TOKEN_X_DECIMALS

    token_x = deploy("deploy_token_x", "DemoERC20", TOKEN_X_NAME, TOKEN_X_SYMBOL, TOKEN_X_DECIMALS, deployer.address)
    router = deploy("deploy_router", "DemoSwapRouter", deployer.address)
    call("set_rate", router, "DemoSwapRouter", "setRate", usdc, token_x, int(rate_e18))
    if router_tokenx_reserve > 0:
        call("mint_router_reserve", token_x, "DemoERC20", "mint", router, int(router_tokenx_reserve))
    gate = deploy("deploy_gate", "EvidenceGate", deployer.address, authorizer, pauser, [router, usdc])
    funded = 0
    if usdc_is_demo and gate_usdc_funding > 0:
        call("fund_gate", usdc, "DemoERC20", "mint", gate, int(gate_usdc_funding))
        funded = int(gate_usdc_funding)
    elif not usdc_is_demo:
        log.warning("existing USDC %s: the gate was not funded (transfer USDC to %s)", usdc, gate)

    return Deployment(
        chain_id=chain_id,
        gate=gate,
        usdc=usdc,
        token_x=token_x,
        router=router,
        deployer=deployer.address,
        authorizer=authorizer,
        pauser=pauser,
        executor=executor,
        txs=txs,
        block_number=last_block,
        deployed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        network=network,
        usdc_decimals=int(usdc_decimals),
        token_x_decimals=TOKEN_X_DECIMALS,
        rate_e18=int(rate_e18),
        gate_usdc_funding=funded,
        router_tokenx_reserve=int(router_tokenx_reserve),
        usdc_is_demo=usdc_is_demo,
    )


# =============================================================================================
# ChainInfo
# =============================================================================================


def chain_info_from_deployment(
    deployment: Deployment,
    *,
    mode: ChainMode,
    authorizer: Optional[str] = None,
    executor: Optional[str] = None,
    network_name: Optional[str] = None,
    explorer_url: Optional[str] = None,
) -> ChainInfo:
    """ChainInfo for a deployment. ``authorizer`` / ``executor`` default to the deployment's roles
    (pass the addresses of the configured signer keys)."""
    return ChainInfo(
        mode=mode,
        chain_id=int(deployment.chain_id),
        network_name=network_name or NETWORK_NAMES.get(mode) or deployment.network or mode.value.lower(),
        gate_address=to_checksum_address(deployment.gate),
        usdc_address=to_checksum_address(deployment.usdc),
        token_x_address=to_checksum_address(deployment.token_x),
        router_address=to_checksum_address(deployment.router),
        explorer_url=None if mode == ChainMode.LOCAL_EVM else explorer_url,
        authorizer=to_checksum_address(authorizer or deployment.authorizer),
        executor=to_checksum_address(executor or deployment.executor),
        deployment=deployment.to_dict(),
        usdc_decimals=int(deployment.usdc_decimals),
        token_x_decimals=int(deployment.token_x_decimals),
    )


def _merge_address(field_name: str, env_name: str, explicit: Optional[str], from_file: Optional[str]) -> Optional[str]:
    if explicit and not is_address(explicit):
        raise ConfigurationError(f"{env_name} is not an EVM address: {explicit!r}")
    if explicit and from_file and explicit.lower() != from_file.lower():
        raise ConfigurationError(
            f"{env_name}={explicit} conflicts with the deployment file ({field_name}={from_file}); "
            "remove one of them"
        )
    value = explicit or from_file
    return to_checksum_address(value) if value else None


def monad_explorer_url(settings: Any, mode: ChainMode) -> str:
    configured = (settings.monad_explorer_url or "").rstrip("/")
    if mode == ChainMode.MONAD_MAINNET and (not configured or "testnet" in configured):
        log.warning("MONAD_MAINNET: MONAD_EXPLORER_URL points at a testnet explorer; using %s", MONAD_MAINNET_EXPLORER)
        return MONAD_MAINNET_EXPLORER
    return configured or MONAD_TESTNET_EXPLORER


def chain_info_from_settings(
    settings: Any,
    deployment: Optional[Deployment] = None,
    *,
    authorizer: Optional[str] = None,
    executor: Optional[str] = None,
) -> ChainInfo:
    """ChainInfo for a Monad mode from settings (+ optional deployment file). Raises ConfigurationError.

    Addresses come from the deployment file and/or explicit settings (``EVIDENCE_GATE_ADDRESS``,
    ``USDC_ADDRESS``, ``TOKEN_X_ADDRESS``, ``SWAP_ROUTER_ADDRESS``); when both are given they must agree.
    The chain id comes from the deployment file when present, else ``MONAD_CHAIN_ID``.
    """
    mode = settings.chain_mode
    if mode not in (ChainMode.MONAD_TESTNET, ChainMode.MONAD_MAINNET):
        raise ConfigurationError(f"chain_info_from_settings is for Monad modes, got {mode.value}")
    gate = _merge_address("gate", "EVIDENCE_GATE_ADDRESS", settings.evidence_gate_address, deployment and deployment.gate)
    usdc = _merge_address("usdc", "USDC_ADDRESS", settings.usdc_address, deployment and deployment.usdc)
    token_x = _merge_address("token_x", "TOKEN_X_ADDRESS", settings.token_x_address, deployment and deployment.token_x)
    router = _merge_address("router", "SWAP_ROUTER_ADDRESS", settings.swap_router_address, deployment and deployment.router)
    if not gate:
        raise ConfigurationError(
            f"{mode.value}: no EvidenceGate address. Set DEPLOYMENT_FILE (written by scripts/deploy.py) "
            "or EVIDENCE_GATE_ADDRESS"
        )
    adapter = (settings.execution_adapter or "").strip().lower()
    needed = {"demo_swap": {"USDC_ADDRESS": usdc, "TOKEN_X_ADDRESS": token_x, "SWAP_ROUTER_ADDRESS": router}, "transfer": {"USDC_ADDRESS": usdc}}
    missing = [name for name, value in needed.get(adapter, {}).items() if not value]
    if missing:
        raise ConfigurationError(f"execution adapter '{adapter}' needs {', '.join(missing)} (or a DEPLOYMENT_FILE)")

    chain_id = int(deployment.chain_id) if deployment else int(settings.monad_chain_id)
    if mode == ChainMode.MONAD_MAINNET and chain_id != MONAD_MAINNET_CHAIN_ID:
        raise ConfigurationError(
            f"MONAD_MAINNET expects chain id {MONAD_MAINNET_CHAIN_ID}, configured {chain_id} (set MONAD_CHAIN_ID)"
        )
    if mode == ChainMode.MONAD_TESTNET and chain_id == MONAD_MAINNET_CHAIN_ID:
        raise ConfigurationError("MONAD_TESTNET is configured with the mainnet chain id 143")

    auth_addr = authorizer or (deployment.authorizer if deployment else None)
    exec_addr = executor or (deployment.executor if deployment else None)
    if not auth_addr or not exec_addr:
        raise ConfigurationError(f"{mode.value}: authorizer and executor addresses are required")
    if deployment and authorizer and deployment.authorizer.lower() != authorizer.lower():
        log.warning(
            "authorizer key %s differs from the deployment's authorizer %s: authorize() will revert unless "
            "AUTHORIZER_ROLE was granted to it", authorizer, deployment.authorizer,
        )
    record = deployment.to_dict() if deployment else {}
    return ChainInfo(
        mode=mode,
        chain_id=chain_id,
        network_name=NETWORK_NAMES[mode],
        gate_address=gate,
        usdc_address=usdc,
        token_x_address=token_x,
        router_address=router,
        explorer_url=monad_explorer_url(settings, mode),
        authorizer=to_checksum_address(auth_addr),
        executor=to_checksum_address(exec_addr),
        deployment=record,
        usdc_decimals=int(deployment.usdc_decimals) if deployment else 6,
        token_x_decimals=int(deployment.token_x_decimals) if deployment else TOKEN_X_DECIMALS,
    )


__all__ = [
    "DEFAULT_RATE_E18",
    "Deployment",
    "DeploymentError",
    "LOCAL_NETWORK_NAME",
    "MONAD_MAINNET_CHAIN_ID",
    "MONAD_MAINNET_EXPLORER",
    "MONAD_MAINNET_RPC",
    "MONAD_TESTNET_CHAIN_ID",
    "MONAD_TESTNET_EXPLORER",
    "MONAD_TESTNET_RPC",
    "chain_info_from_deployment",
    "chain_info_from_settings",
    "deploy_suite",
    "load_deployment",
]
