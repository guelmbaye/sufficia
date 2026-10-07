#!/usr/bin/env python3
"""Deploys the SUFFICIA demo suite (dUSDC, TOKEN-X, DemoSwapRouter, EvidenceGate) and writes
``deployments/<network>.json`` for the backend (``DEPLOYMENT_FILE``).

Usage (from apps/api):

    python scripts/deploy.py --network monad-testnet [--rpc URL] [--out deployments/monad-testnet.json] [--usdc 0x...]
    python scripts/deploy.py --network monad-mainnet --rpc https://rpc.monad.xyz --yes-mainnet
    python scripts/deploy.py --network local            # in-process py-evm chain, dev keys (smoke test)

Environment:
    DEPLOYER_PRIVATE_KEY     required for Monad (pays gas, becomes DEFAULT_ADMIN of the gate and owner of the
                             demo tokens / router); LOCAL_DEV_KEYS["deployer"] for --network local
    AUTHORIZER_ADDRESS       AUTHORIZER_ROLE holder (the backend signer); or AUTHORIZER_PRIVATE_KEY
    EXECUTOR / EXECUTOR_ADDRESS   the executor recorded in the deployment; or EXECUTOR_PRIVATE_KEY
    PAUSER / PAUSER_ADDRESS  PAUSER_ROLE holder; or PAUSER_PRIVATE_KEY; defaults to the deployer
    MONAD_RPC_URL            default RPC when --rpc is not given (Monad networks)
    (each variable may also be written in apps/api/.env; the process environment wins)
    GATE_USDC_FUNDING        dUSDC base units minted to the gate (default 100_000 dUSDC on Monad = 1,000 Grand Prize
                             executions of 100 USDC; 1_000 dUSDC for --network local). Top up later: scripts/fund_gate.py
    ROUTER_TOKENX_RESERVE    TOKEN-X base units minted to the router (default 1_000_000 TOKEN-X)

Private keys are read from the environment only (never from the command line) and never printed.
Exit codes: 0 success, 2 configuration error, 3 RPC unreachable / wrong chain, 4 a deployment step failed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from eth_account import Account  # noqa: E402
from eth_utils import is_address, to_checksum_address  # noqa: E402

from sufficia.blockchain.deploy import (  # noqa: E402
    MONAD_MAINNET_CHAIN_ID,
    MONAD_MAINNET_EXPLORER,
    MONAD_MAINNET_RPC,
    MONAD_TESTNET_CHAIN_ID,
    MONAD_TESTNET_EXPLORER,
    MONAD_TESTNET_RPC,
    Deployment,
    DeploymentError,
    deploy_suite,
)
from sufficia.blockchain.gateway import ChainUnavailable  # noqa: E402
from sufficia.blockchain.web3_gateway import ConfigurationError, FailoverProvider, http_providers  # noqa: E402
from sufficia.devtools.envfile import tool_env  # noqa: E402

REPO_ROOT = API_ROOT.parents[1]
DEPLOYMENTS_DIR = REPO_ROOT / "deployments"
MONAD_DEMO_GATE_FUNDING = 100_000 * 10**6  # demo dUSDC (6 decimals): each Grand Prize execution spends 100

NETWORKS = {
    "monad-testnet": {"chain_id": MONAD_TESTNET_CHAIN_ID, "rpc": MONAD_TESTNET_RPC, "explorer": MONAD_TESTNET_EXPLORER},
    "monad-mainnet": {"chain_id": MONAD_MAINNET_CHAIN_ID, "rpc": MONAD_MAINNET_RPC, "explorer": MONAD_MAINNET_EXPLORER},
    "local": {"chain_id": None, "rpc": None, "explorer": None},
}


class CliError(Exception):
    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


def _env(env: dict[str, str], *names: str) -> Optional[str]:
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            return value
    return None


def _key_address(key: str, name: str) -> str:
    try:
        return Account.from_key(key).address
    except Exception:  # noqa: BLE001 - never echo key material
        raise CliError(f"{name} is not a valid private key") from None


def _role_address(env: dict[str, str], role: str, *, address_vars: Sequence[str], key_var: str, default: Optional[str]) -> str:
    value = _env(env, *address_vars)
    if value:
        if not is_address(value):
            raise CliError(f"{address_vars[0]} is not an EVM address: {value!r}")
        return to_checksum_address(value)
    key = _env(env, key_var)
    if key:
        return _key_address(key, key_var)
    if default:
        return default
    raise CliError(f"{role}: set {' or '.join(address_vars)} or {key_var}")


def _int_env(env: dict[str, str], name: str) -> Optional[int]:
    value = _env(env, name)
    if value is None:
        return None
    try:
        return int(value.replace("_", ""))
    except ValueError:
        raise CliError(f"{name} must be an integer (base units), got {value!r}") from None


def _explorer(base: Optional[str], kind: str, value: str) -> str:
    return f"{base.rstrip('/')}/{kind}/{value}" if base else ""


def main(argv: Optional[Sequence[str]] = None, *, env: Optional[dict[str, str]] = None, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description="Deploy the SUFFICIA EvidenceGate demo suite.")
    parser.add_argument("--network", required=True, choices=sorted(NETWORKS))
    parser.add_argument("--rpc", help="RPC URL (Monad networks; default MONAD_RPC_URL or the public endpoint)")
    parser.add_argument("--out", help="output JSON (default: <repo>/deployments/<network>.json)")
    parser.add_argument("--usdc", help="use an existing 6-decimals USDC instead of deploying demo dUSDC")
    parser.add_argument("--timeout", type=float, default=180.0, help="per-transaction confirmation timeout (s)")
    parser.add_argument("--yes-mainnet", action="store_true", help="required to deploy on Monad mainnet")
    args = parser.parse_args(argv)
    env = tool_env(API_ROOT) if env is None else dict(env)

    try:
        return _run(args, env, out)
    except CliError as exc:
        out(f"error: {exc}")
        return exc.code
    except ConfigurationError as exc:
        out(f"error: {exc}")
        return 2
    except ChainUnavailable as exc:
        out(f"error: RPC unreachable: {exc}")
        return 3
    except DeploymentError as exc:
        out(f"error: deployment failed at step '{exc.step}': {exc}")
        for step, tx_hash in exc.partial.items():
            out(f"  already sent {step}: {tx_hash}")
        return 4


def _run(args: argparse.Namespace, env: dict[str, str], out: Callable[[str], None]) -> int:
    from web3 import Web3

    network = NETWORKS[args.network]
    local_chain = None
    if args.network == "local":
        from sufficia.blockchain.local_evm import LocalEvmChain
        from sufficia.config import LOCAL_DEV_KEYS

        deployer_key = _env(env, "DEPLOYER_PRIVATE_KEY") or LOCAL_DEV_KEYS["deployer"]
        dev_defaults = {r: Account.from_key(LOCAL_DEV_KEYS[r]).address for r in ("authorizer", "executor", "pauser")}
        local_chain = LocalEvmChain({"deployer": deployer_key}, deploy=False, tx_timeout=args.timeout)
        w3 = local_chain.w3
        rpc_label = "in-process py-evm (not Monad)"
    else:
        if args.network == "monad-mainnet" and not args.yes_mainnet:
            raise CliError("refusing to deploy on Monad MAINNET without --yes-mainnet")
        deployer_key = _env(env, "DEPLOYER_PRIVATE_KEY")
        if not deployer_key:
            raise CliError("DEPLOYER_PRIVATE_KEY is required")
        dev_defaults = {}
        rpc = args.rpc or _env(env, "MONAD_RPC_URL") or network["rpc"]
        providers, labels = http_providers([rpc], timeout=30.0)
        w3 = Web3(FailoverProvider(providers, labels=labels))
        rpc_label = labels[0]

    deployer = _key_address(deployer_key, "DEPLOYER_PRIVATE_KEY")
    authorizer = _role_address(
        env, "authorizer", address_vars=("AUTHORIZER_ADDRESS", "AUTHORIZER"), key_var="AUTHORIZER_PRIVATE_KEY",
        default=dev_defaults.get("authorizer"),
    )
    executor = _role_address(
        env, "executor", address_vars=("EXECUTOR", "EXECUTOR_ADDRESS"), key_var="EXECUTOR_PRIVATE_KEY",
        default=dev_defaults.get("executor"),
    )
    pauser = _role_address(
        env, "pauser", address_vars=("PAUSER", "PAUSER_ADDRESS"), key_var="PAUSER_PRIVATE_KEY",
        default=dev_defaults.get("pauser") or deployer,
    )
    if args.usdc and not is_address(args.usdc):
        raise CliError(f"--usdc is not an EVM address: {args.usdc!r}")

    chain_id = int(w3.eth.chain_id)
    expected = network["chain_id"]
    if expected is not None and chain_id != expected:
        raise CliError(f"RPC {rpc_label} serves chain {chain_id}, expected {expected} for {args.network}", code=3)
    balance = int(w3.eth.get_balance(deployer))
    if args.network != "local" and balance == 0:
        raise CliError(f"deployer {deployer} has no MON for gas on {args.network}")

    explorer = network["explorer"]
    out(f"network     {args.network} (chain {chain_id}) via {rpc_label}")
    out(f"deployer    {deployer}  balance {balance / 10**18:.4f}")
    out(f"authorizer  {authorizer}")
    out(f"executor    {executor}")
    out(f"pauser      {pauser}")

    def on_step(step: str, outcome) -> None:  # noqa: ANN001
        where = f" -> {outcome.contract_address}" if outcome.contract_address else ""
        link = _explorer(explorer, "tx", outcome.tx_hash)
        out(f"  ok {step:<20} {outcome.tx_hash}{where}" + (f"  {link}" if link else ""))

    deployment: Deployment = deploy_suite(
        w3,
        deployer_key=deployer_key,
        authorizer=authorizer,
        pauser=pauser,
        executor=executor,
        existing_usdc=args.usdc,
        gate_usdc_funding=_int_env(env, "GATE_USDC_FUNDING") or (None if local_chain or args.usdc else MONAD_DEMO_GATE_FUNDING),
        router_tokenx_reserve=_int_env(env, "ROUTER_TOKENX_RESERVE"),
        network=args.network,
        tx_timeout=args.timeout,
        poll_interval=0.01 if local_chain else 1.0,
        before_tx=local_chain.bootstrap_clock if local_chain else None,
        on_step=on_step,
    )

    target = Path(args.out) if args.out else DEPLOYMENTS_DIR / f"{args.network}.json"
    if not target.is_absolute():
        target = Path.cwd() / target
    deployment.save(target)

    out("")
    for label, address in (
        ("EvidenceGate", deployment.gate),
        ("USDC", deployment.usdc),
        ("TOKEN-X", deployment.token_x),
        ("DemoSwapRouter", deployment.router),
    ):
        link = _explorer(explorer, "address", address)
        out(f"{label:<15} {address}" + (f"  {link}" if link else ""))
    if not deployment.usdc_is_demo:
        out(f"note: existing USDC was not minted; transfer USDC to the gate {deployment.gate} to fund executions")
    out(f"\nwrote {target}")
    if args.network != "local":
        mode = "MONAD_MAINNET" if args.network == "monad-mainnet" else "MONAD_TESTNET"
        out(f"backend: CHAIN_MODE={mode} DEPLOYMENT_FILE={target}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
