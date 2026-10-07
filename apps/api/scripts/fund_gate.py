#!/usr/bin/env python3
"""Mints demo dUSDC to the EvidenceGate treasury (each Grand Prize execution spends 100).

Usage (from apps/api):

    python scripts/fund_gate.py --amount 10000                 # whole dUSDC
    python scripts/fund_gate.py --amount 10000 --deployment ../../deployments/monad-testnet.json

Needs DEPLOYER_PRIVATE_KEY (the owner of the demo dUSDC), from the environment or apps/api/.env.
Only for a deployment whose USDC is the demo token; a real USDC is funded by transfer.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Optional, Sequence

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from web3 import Web3  # noqa: E402

from sufficia.blockchain.artifacts import contract_abi, describe_revert  # noqa: E402
from sufficia.blockchain.deploy import MONAD_TESTNET_RPC, load_deployment  # noqa: E402
from sufficia.blockchain.web3_gateway import ConfigurationError, FailoverProvider, TransactionSender, as_account, http_providers  # noqa: E402
from sufficia.devtools.envfile import tool_env  # noqa: E402

REPO_ROOT = API_ROOT.parents[1]


def main(argv: Optional[Sequence[str]] = None, *, out: Callable[[str], None] = print, w3: Optional[Web3] = None) -> int:
    parser = argparse.ArgumentParser(description="Mint demo dUSDC to the EvidenceGate.")
    parser.add_argument("--amount", required=True, help="whole dUSDC to mint, e.g. 10000")
    parser.add_argument("--deployment", default=None, help="deployment JSON (default DEPLOYMENT_FILE or deployments/monad-testnet.json)")
    parser.add_argument("--rpc", default=None)
    args = parser.parse_args(argv)
    env = tool_env(API_ROOT)
    try:
        amount = Decimal(args.amount)
        if amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        out(f"error: --amount must be a positive number of dUSDC, got {args.amount!r}")
        return 2
    path = args.deployment or env.get("DEPLOYMENT_FILE") or str(REPO_ROOT / "deployments" / "monad-testnet.json")
    try:
        deployment = load_deployment(Path(path) if Path(path).is_absolute() else (Path.cwd() / path if (Path.cwd() / path).is_file() else REPO_ROOT / path))
    except ConfigurationError as exc:
        out(f"error: {exc}")
        return 2
    if not deployment.usdc_is_demo:
        out("error: this deployment uses an external USDC, which cannot be minted; transfer USDC to the gate instead")
        return 2
    key = env.get("DEPLOYER_PRIVATE_KEY")
    if not key:
        out("error: DEPLOYER_PRIVATE_KEY is required (the owner of the demo dUSDC)")
        return 2
    try:
        deployer = as_account(key)
    except Exception:  # noqa: BLE001 - never echo key material
        out("error: DEPLOYER_PRIVATE_KEY is not a valid private key")
        return 2
    if w3 is None:
        providers, labels = http_providers([args.rpc or env.get("MONAD_RPC_URL") or MONAD_TESTNET_RPC, env.get("MONAD_RPC_FALLBACK_URL")], timeout=30.0)
        w3 = Web3(FailoverProvider(providers, labels=labels))
    if int(w3.eth.chain_id) != deployment.chain_id:
        out(f"error: the RPC serves chain {w3.eth.chain_id}, the deployment is on chain {deployment.chain_id}")
        return 3

    units = int(amount * (10 ** int(deployment.usdc_decimals)))
    usdc = w3.eth.contract(address=Web3.to_checksum_address(deployment.usdc), abi=contract_abi("DemoERC20"))
    gate = Web3.to_checksum_address(deployment.gate)
    before = int(usdc.functions.balanceOf(gate).call())
    sender = TransactionSender(w3, deployment.chain_id, tx_timeout=180.0, poll_interval=1.0)
    outcome = sender.send(deployer, to=usdc.address, data=usdc.encode_abi("mint", args=[gate, units]))
    if not outcome.ok:
        out(f"error: mint failed: {outcome.error or describe_revert(outcome.error_name, outcome.error_args)} (tx {outcome.tx_hash})")
        return 4
    after = int(usdc.functions.balanceOf(gate).call())
    scale = Decimal(10 ** int(deployment.usdc_decimals))
    out(f"minted {amount} dUSDC to the gate {gate} in tx {outcome.tx_hash}")
    out(f"gate treasury {Decimal(before) / scale:,} -> {Decimal(after) / scale:,} dUSDC")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
