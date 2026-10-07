#!/usr/bin/env python3
"""Monad testnet wallets, step by step (from apps/api):

    python scripts/testnet.py keys          # creates the 4 testnet wallets in apps/api/.env, prints their addresses
    python scripts/testnet.py status        # balances, what is missing, and the next command to run
    python scripts/testnet.py distribute    # sends MON from the deployer to the authorizer, executor and pauser
    python scripts/testnet.py mode monad    # CHAIN_MODE=MONAD_TESTNET in apps/api/.env (``mode local`` goes back)

Only the deployer needs faucet MON: ``distribute`` keeps the deployment cost on it and sends the others what
they need for ``--runs`` executed runs (default 20). Private keys stay in apps/api/.env and are never printed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from web3 import Web3  # noqa: E402

from sufficia.blockchain.deploy import MONAD_TESTNET_CHAIN_ID, MONAD_TESTNET_EXPLORER, MONAD_TESTNET_RPC  # noqa: E402
from sufficia.blockchain.web3_gateway import FailoverProvider, http_providers  # noqa: E402
from sufficia.devtools.envfile import tool_env  # noqa: E402
from sufficia.devtools.preflight import find_deployment_file  # noqa: E402
from sufficia.devtools.wallets import (  # noqa: E402
    DEFAULT_RUNS,
    KEY_VARS,
    ROLE_PURPOSE,
    ROLES,
    FundingPlan,
    balances_of,
    create_keys,
    fees,
    mon,
    mon_rounded_up,
    plan_funding,
    runs_affordable,
    send_transfers,
    set_chain_mode,
)

REPO_ROOT = API_ROOT.parents[1]
ENV_PATH = API_ROOT / ".env"
FAUCETS = ("https://faucet.monad.xyz", "https://faucet.quicknode.com/monad/testnet")


READY_RUNS = 10  # the bar preflight marks OK: below it, status asks for a top-up


class Context:
    def __init__(self, w3: Web3, chain_id: int, env: dict[str, str], addresses: dict[str, str], explorer: str, deployed: bool, deployment_path: Optional[Path], contracts: str):
        self.w3, self.chain_id, self.env, self.addresses = w3, chain_id, env, addresses
        self.explorer, self.deployed, self.deployment_path, self.contracts = explorer, deployed, deployment_path, contracts


def _connect(args: argparse.Namespace, out: Callable[[str], None], w3: Optional[Web3]) -> Optional[Context]:
    env = tool_env(ENV_PATH.parent)
    from eth_account import Account

    addresses: dict[str, str] = {}
    for role in ROLES:
        key = env.get(KEY_VARS[role])
        if not key:
            out(f"{KEY_VARS[role]} is not set: create the wallets first with  python scripts/testnet.py keys")
            return None
        try:
            addresses[role] = Account.from_key(key).address
        except Exception:  # noqa: BLE001 - never echo key material
            out(f"{KEY_VARS[role]} in apps/api/.env is not a valid private key")
            return None
    expected = int(env.get("MONAD_CHAIN_ID") or MONAD_TESTNET_CHAIN_ID)
    if w3 is None:
        providers, labels = http_providers([args.rpc or env.get("MONAD_RPC_URL") or MONAD_TESTNET_RPC, None if args.rpc else env.get("MONAD_RPC_FALLBACK_URL")], timeout=30.0)
        w3 = Web3(FailoverProvider(providers, labels=labels))
    try:
        chain_id = int(w3.eth.chain_id)
    except Exception as exc:  # noqa: BLE001
        out(f"cannot reach the Monad RPC ({type(exc).__name__}); check the internet connection and MONAD_RPC_URL")
        return None
    if chain_id != expected:
        out(f"the RPC serves chain {chain_id}, expected {expected} (Monad testnet is 10143); check MONAD_RPC_URL")
        return None
    path = find_deployment_file(None, env, "monad-testnet", repo_root=REPO_ROOT, api_root=API_ROOT)
    deployed, contracts = False, "not deployed yet"
    if path is not None:
        from sufficia.blockchain.deploy import load_deployment

        try:
            deployment = load_deployment(path)
        except Exception:  # noqa: BLE001 - a broken file reads as "not deployed"; preflight explains it
            deployment = None
            contracts = f"unreadable deployment file {path}: deploy again"
        if deployment is not None:
            if deployment.chain_id != chain_id:
                contracts = f"{path} is for chain {deployment.chain_id}: deploy again"
            elif len(w3.eth.get_code(Web3.to_checksum_address(deployment.gate))) == 0:
                contracts = f"no contract at the address in {path} (testnet reset?): deploy again"
            elif deployment.authorizer.lower() != addresses["authorizer"].lower():
                contracts = f"deployed for other wallets ({path}): deploy again for these"
            else:
                deployed, contracts = True, f"deployed ({path})"
    explorer = (env.get("MONAD_EXPLORER_URL") or MONAD_TESTNET_EXPLORER).rstrip("/")
    return Context(w3, chain_id, env, addresses, explorer, deployed, path, contracts)


def _plan(ctx: Context, runs: int) -> tuple[dict[str, int], FundingPlan]:
    balances = balances_of(ctx.w3, ctx.addresses)
    price, bid = fees(ctx.w3)
    return balances, plan_funding(balances, ctx.addresses, price=price, bid=bid, deployed=ctx.deployed, runs=runs)


def _faucet_lines(ctx: Context, amount: int) -> list[str]:
    return [
        f"The deployer needs about {mon_rounded_up(amount)} more. Get testnet MON for the DEPLOYER address:",
        f"    {ctx.addresses['deployer']}",
        "  faucets: " + "  ".join(FAUCETS),
        "  then run  python scripts/testnet.py status  again (the drip can take a minute to show).",
    ]


# ---------------------------------------------------------------------------------------------


def cmd_keys(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    try:
        result = create_keys(ENV_PATH)
    except ValueError as exc:
        out(f"error: {exc}")
        return 2
    if result.created:
        out(f"Created {len(result.created)} testnet wallet(s) in {ENV_PATH} (the private keys are in that file only):")
    else:
        out(f"The 4 wallets already exist in {ENV_PATH}; nothing was replaced:")
    for role in ROLES:
        tag = "new " if role in result.created else "kept"
        out(f"  {tag}  {role:<10} {result.addresses[role]}   {ROLE_PURPOSE[role]}")
    if result.settings_written:
        out("Monad testnet settings added: " + ", ".join(result.settings_written))
    if (result.chain_mode or "").upper() != "MONAD_TESTNET":
        out(f"CHAIN_MODE is {result.chain_mode or 'not set (LOCAL_EVM)'}: the app keeps running on the local chain until  python scripts/testnet.py mode monad")
    out("")
    out("Next: get testnet MON for the DEPLOYER address only:")
    out(f"    {result.addresses['deployer']}")
    out("  faucets: " + "  ".join(FAUCETS))
    out("  then:    python scripts/testnet.py status")
    return 0


def cmd_status(args: argparse.Namespace, out: Callable[[str], None], w3: Optional[Web3] = None) -> int:
    ctx = _connect(args, out, w3)
    if ctx is None:
        return 2
    balances, plan = _plan(ctx, args.runs)
    out(f"Monad chain {ctx.chain_id}, gas price {plan.price / 1e9:.1f} gwei (charged on the gas limit)")
    out(f"Contracts: {ctx.contracts}")
    out("")
    runs_now = min(runs_affordable(balances[role], role, plan.price) for role in ("authorizer", "executor"))
    for role in ROLES:
        if role == "deployer":
            note = f"keeps {mon(plan.deployer_keep)} for {'the deployment and ' if not ctx.deployed else ''}a gate top-up"
        elif role == "pauser":
            note = "only needs MON to pause"
        else:
            note = f"about {runs_affordable(balances[role], role, plan.price)} executed runs"
        out(f"  {role:<10} {ctx.addresses[role]}  {mon(balances[role]):>14}   {note}")
    out(f"  explorer   {ctx.explorer}/address/{ctx.addresses['deployer']}")
    out("")

    mode = (ctx.env.get("CHAIN_MODE") or "LOCAL_EVM").upper()
    cannot_deploy = not ctx.deployed and balances["deployer"] < plan.deployer_keep
    if cannot_deploy or (runs_now < 1 and plan.funded_runs == 0):
        for line in _faucet_lines(ctx, max(plan.missing, plan.deployer_keep - balances["deployer"])):
            out(line)
        return 1
    if runs_now < READY_RUNS and plan.transfers:
        out(f"Next: python scripts/testnet.py distribute   (sends {mon(plan.total)} from the deployer, enough for {plan.funded_runs} runs)")
        if plan.missing:
            out(f"      or first add about {mon_rounded_up(plan.missing)} to the deployer for {plan.requested_runs} runs")
        return 0
    if not ctx.deployed:
        out("Next: python scripts/preflight.py --no-deployment")
        out("      python scripts/deploy.py --network monad-testnet")
        return 0
    if mode != "MONAD_TESTNET":
        out("Next: python scripts/testnet.py mode monad    (then start the API)")
        return 0
    if runs_now < READY_RUNS:
        out(f"Low: about {runs_now} executed runs left. Get testnet MON for the deployer, then python scripts/testnet.py distribute")
    out("Ready: python scripts/preflight.py, then start the API (python -m uvicorn sufficia.api.app:app --port 8000) and the web app")
    return 0


def cmd_distribute(args: argparse.Namespace, out: Callable[[str], None], w3: Optional[Web3] = None, blocks_between: Optional[int] = None) -> int:
    ctx = _connect(args, out, w3)
    if ctx is None:
        return 2
    balances, plan = _plan(ctx, args.runs)
    if plan.funded_runs == 0:
        out(f"The deployer holds {mon(balances['deployer'])}: not enough to keep {mon(plan.deployer_keep)} and fund even one run.")
        for line in _faucet_lines(ctx, plan.missing):
            out(line)
        return 1
    if not plan.transfers:
        out(f"Nothing to send: the authorizer, executor and pauser already hold enough for {plan.requested_runs} runs.")
        return 0
    if plan.funded_runs < plan.requested_runs:
        out(f"The deployer can fund {plan.funded_runs} runs now (add about {mon_rounded_up(plan.missing)} for {plan.requested_runs}).")
    out(f"From the deployer {ctx.addresses['deployer']} ({mon(balances['deployer'])}), keeping {mon(plan.deployer_keep)}:")
    for t in plan.transfers:
        out(f"  {t.role:<10} {t.to}  +{mon(t.amount)}")
    if args.dry_run:
        out("(dry run: nothing sent)")
        return 0
    kwargs = {} if blocks_between is None else {"blocks_between": blocks_between}
    results = send_transfers(ctx.w3, ctx.chain_id, ctx.env[KEY_VARS["deployer"]], plan.transfers, out=out, **kwargs)
    if any(not outcome.ok for _, outcome in results):
        out("A transfer did not go through. Wait ten seconds and run the same command again: it only sends what is still missing.")
        return 4
    after = balances_of(ctx.w3, ctx.addresses)
    out("")
    for role in ROLES:
        out(f"  {role:<10} {mon(after[role]):>14}")
    out("")
    out("Next: python scripts/preflight.py --no-deployment" if not ctx.deployed else "Next: python scripts/preflight.py")
    return 0


def cmd_mode(args: argparse.Namespace, out: Callable[[str], None]) -> int:
    value = set_chain_mode(ENV_PATH, args.mode)
    out(f"CHAIN_MODE={value} written to {ENV_PATH}. Restart the API for it to take effect.")
    if args.mode == "monad":
        env = tool_env(ENV_PATH.parent)
        if find_deployment_file(None, env, "monad-testnet", repo_root=REPO_ROOT, api_root=API_ROOT) is None:
            out("Note: no deployments/monad-testnet.json yet; deploy first (python scripts/deploy.py --network monad-testnet).")
    return 0


def main(argv: Optional[Sequence[str]] = None, *, out: Callable[[str], None] = print, w3: Optional[Web3] = None, blocks_between: Optional[int] = None) -> int:
    parser = argparse.ArgumentParser(description="SUFFICIA Monad testnet wallets.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("keys", help="create the four testnet wallets in apps/api/.env")
    for name, text in (("status", "balances and the next step"), ("distribute", "send MON from the deployer to the other wallets")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--runs", type=int, default=DEFAULT_RUNS, help=f"executed runs to fund (default {DEFAULT_RUNS})")
        p.add_argument("--rpc", default=None, help="RPC URL (default MONAD_RPC_URL)")
        if name == "distribute":
            p.add_argument("--dry-run", action="store_true", help="show the transfers without sending them")
    p = sub.add_parser("mode", help="run the app on Monad testnet (monad) or the local chain (local)")
    p.add_argument("mode", choices=["monad", "local"])
    args = parser.parse_args(argv)
    if getattr(args, "runs", 1) < 1:
        parser.error("--runs must be at least 1")
    if args.command == "keys":
        return cmd_keys(args, out)
    if args.command == "status":
        return cmd_status(args, out, w3)
    if args.command == "distribute":
        return cmd_distribute(args, out, w3, blocks_between)
    return cmd_mode(args, out)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
