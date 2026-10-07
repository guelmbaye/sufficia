#!/usr/bin/env python3
"""Read-only readiness check for Monad: run it before deploying and again before a demo.

Usage (from apps/api):

    python scripts/preflight.py                         # RPCs, chain id, clock, keys, balances (+ the deployment if it exists)
    python scripts/preflight.py --deployment ../../deployments/monad-testnet.json
    python scripts/preflight.py --no-deployment         # before the first deployment

Reads the same variables as the API and scripts/deploy.py (process environment over apps/api/.env):
MONAD_RPC_URL, MONAD_RPC_FALLBACK_URL, DEPLOYER/AUTHORIZER/EXECUTOR/PAUSER_PRIVATE_KEY, DEPLOYMENT_FILE.
Never signs, never sends, never prints key material. Exit code 0 when nothing FAILs, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from web3 import Web3  # noqa: E402

from sufficia.blockchain.deploy import (  # noqa: E402
    MONAD_MAINNET_CHAIN_ID,
    MONAD_MAINNET_RPC,
    MONAD_TESTNET_CHAIN_ID,
    MONAD_TESTNET_RPC,
    load_deployment,
)
from sufficia.blockchain.web3_gateway import ConfigurationError, http_providers  # noqa: E402
from sufficia.devtools.envfile import tool_env  # noqa: E402
from sufficia.devtools.preflight import FAIL, OK, WARN, Check, find_deployment_file, run_checks, summarize  # noqa: E402

REPO_ROOT = API_ROOT.parents[1]
NETWORKS = {
    "monad-testnet": (MONAD_TESTNET_CHAIN_ID, MONAD_TESTNET_RPC),
    "monad-mainnet": (MONAD_MAINNET_CHAIN_ID, MONAD_MAINNET_RPC),
}


def _deployment_path(arg: Optional[str], env: dict[str, str], network: str) -> Optional[Path]:
    try:  # an explicit path that does not exist is an error, a default one is not
        return find_deployment_file(arg, env, network, repo_root=REPO_ROOT, api_root=API_ROOT)
    except FileNotFoundError as exc:
        raise ConfigurationError(str(exc)) from None


def main(argv: Optional[Sequence[str]] = None, *, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description="Read-only Monad readiness check for SUFFICIA.")
    parser.add_argument("--network", default="monad-testnet", choices=sorted(NETWORKS))
    parser.add_argument("--rpc", help="RPC URL (default MONAD_RPC_URL, then the public endpoint)")
    parser.add_argument("--deployment", help="deployment JSON (default DEPLOYMENT_FILE or deployments/<network>.json)")
    parser.add_argument("--no-deployment", action="store_true", help="check readiness to deploy only")
    args = parser.parse_args(argv)
    env = tool_env(API_ROOT)
    expected, default_rpc = NETWORKS[args.network]

    urls = [args.rpc or env.get("MONAD_RPC_URL") or default_rpc]
    if not args.rpc and env.get("MONAD_RPC_FALLBACK_URL"):
        urls.append(env["MONAD_RPC_FALLBACK_URL"])
    checks: list[Check] = []
    usable: Optional[Web3] = None
    providers, labels = http_providers(urls, timeout=15.0)
    for provider, label in zip(providers, labels):
        w3 = Web3(provider)
        started = time.perf_counter()
        try:
            chain_id = int(w3.eth.chain_id)
        except Exception as exc:  # noqa: BLE001
            checks.append(Check(FAIL if usable is None and label == labels[-1] else WARN, f"rpc {label}", f"unreachable: {type(exc).__name__}"))
            continue
        ms = (time.perf_counter() - started) * 1000
        good = chain_id == expected
        checks.append(Check(OK if good else FAIL, f"rpc {label}", f"chain {chain_id}, {ms:.0f} ms"))
        if good and usable is None:
            usable = w3

    deployment = None
    if usable is not None:
        try:
            path = None if args.no_deployment else _deployment_path(args.deployment, env, args.network)
            deployment = load_deployment(path) if path else None
            if path:
                checks.append(Check(OK, "deployment file", str(path)))
            elif not args.no_deployment:
                checks.append(Check(WARN, "deployment file", "none yet: run scripts/deploy.py --network " + args.network))
        except ConfigurationError as exc:
            checks.append(Check(FAIL, "deployment file", str(exc)))
        keys = {role: env.get(f"{role.upper()}_PRIVATE_KEY") for role in ("deployer", "authorizer", "executor", "pauser")}
        checks += run_checks(usable, expected_chain_id=expected, keys=keys, deployment=deployment)
    elif not checks:
        checks.append(Check(FAIL, "rpc", "no RPC URL configured"))

    width = max(len(c.name) for c in checks)
    for c in checks:
        out(f"{c.level:<4}  {c.name:<{width}}  {c.detail}")
    counts = summarize(checks)
    out(f"\n{counts[OK]} ok, {counts[WARN]} warnings, {counts[FAIL]} failures")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
