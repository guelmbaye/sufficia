"""The four testnet wallets: create them in ``apps/api/.env``, see what they hold, fund them from the deployer.

Used by ``scripts/testnet.py``. One faucet drip to the *deployer* address is enough to start: ``distribute``
then sends the authorizer, the executor and the pauser what they need, keeping the deployment cost on the
deployer. Amounts follow Monad's fee model (the gas *limit* is charged, base fee >= 100 gwei) with the
measured limits from :mod:`sufficia.devtools.preflight`.

Monad's reserve-balance rule matters for small accounts: below 10 MON, a transfer that lowers the sender's
balance only succeeds when the sender sent nothing else in the previous 3 blocks. Transfers are therefore sent
one at a time, waiting a few blocks between them.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping, Optional

from eth_account import Account
from web3 import Web3

from .envfile import parse_env_file
from .preflight import DEPLOY_GAS_LIMIT, RUN_GAS_LIMIT, price_per_gas

ROLES = ("deployer", "authorizer", "executor", "pauser")
ROLE_PURPOSE = {
    "deployer": "deploys the contracts, becomes their admin, tops up the gate; receives the faucet MON",
    "authorizer": "signs registerIntent, authorize and recordReceipt for the backend",
    "executor": "the only address allowed to execute an authorized action",
    "pauser": "can pause the EvidenceGate in an emergency",
}
KEY_VARS = {role: f"{role.upper()}_PRIVATE_KEY" for role in ROLES}

MONAD_TESTNET_SETTINGS = (
    ("MONAD_RPC_URL", "https://testnet-rpc.monad.xyz"),
    ("MONAD_RPC_FALLBACK_URL", "https://rpc.ankr.com/monad_testnet"),
    ("MONAD_CHAIN_ID", "10143"),
    ("MONAD_EXPLORER_URL", "https://testnet.monadvision.com"),
    ("DEPLOYMENT_FILE", "../../deployments/monad-testnet.json"),
)
CHAIN_MODES = {"monad": "MONAD_TESTNET", "local": "LOCAL_EVM"}

TRANSFER_GAS_LIMIT = 26_250  # 21,000 x 1.25, as the backend sets it
PAUSER_GAS = 200_000  # a pause() plus margin
DEPLOYER_UPKEEP_GAS = 200_000  # a gate top-up (scripts/fund_gate.py) after the deployment
RESERVE_BALANCE_BLOCKS = 3  # Monad "k": a low-balance sender may lower its balance once every k blocks
DEFAULT_RUNS = 20


def mon(wei: int | Decimal) -> str:
    return f"{Decimal(int(wei)) / Decimal(10**18):.4f} MON"


def mon_rounded_up(wei: int) -> str:
    """Amounts to ask a faucet for: rounded up to 0.1 MON."""
    tenths = -(-int(wei) // 10**17)
    return f"{Decimal(tenths) / 10:.1f} MON"


# ---------------------------------------------------------------------------------------------
# .env editing
# ---------------------------------------------------------------------------------------------


def _line_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*(?:export\s+)?{re.escape(name)}\s*=", re.MULTILINE)


def set_env_values(path: Path, values: Mapping[str, str], *, replace: bool, header: Optional[str] = None) -> list[str]:
    """Writes ``values`` into the .env file at ``path`` and returns the names it wrote.

    An existing line is rewritten only when ``replace`` is true or its value is empty; missing names are
    appended (under ``header`` when given). Everything else in the file is left as it was. LF, UTF-8.
    """
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    current = parse_env_file(path)
    written: list[str] = []
    appended: list[str] = []
    for name, value in values.items():
        pattern = _line_pattern(name)
        if pattern.search(text):
            if current.get(name) and not replace:
                continue
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if pattern.match(line):
                    lines[i] = f"{name}={value}"
            text = "\n".join(lines)
        else:
            appended.append(f"{name}={value}")
        written.append(name)
    if appended:
        if text and not text.endswith("\n"):
            text += "\n"
        if header:
            text += ("\n" if text else "") + f"# {header}\n"
        text += "\n".join(appended) + "\n"
    if written:
        path.write_text(text, encoding="utf-8", newline="\n")
    return written


@dataclass
class KeysResult:
    addresses: dict[str, str]
    created: list[str]
    kept: list[str]
    settings_written: list[str]
    chain_mode: Optional[str]


def _new_key() -> str:
    return "0x" + bytes(Account.create(secrets.token_hex(32)).key).hex()


def create_keys(env_path: Path, *, new_key: Callable[[], str] = _new_key) -> KeysResult:
    """Generates the missing wallet keys into the .env file; never replaces a key that is already set.

    Also writes the Monad testnet network settings that are missing. ``CHAIN_MODE`` is not touched (the app
    keeps running locally until ``scripts/testnet.py mode monad``). Raises ValueError for an invalid key in
    the file, so nothing is overwritten by mistake.
    """
    current = parse_env_file(env_path)
    keys: dict[str, str] = {}
    created: list[str] = []
    kept: list[str] = []
    for role in ROLES:
        existing = current.get(KEY_VARS[role], "").strip()
        if existing:
            try:
                Account.from_key(existing)
            except Exception:  # noqa: BLE001 - never echo key material
                raise ValueError(f"{KEY_VARS[role]} in {env_path} is not a valid private key; fix or delete that line") from None
            keys[role] = existing
            kept.append(role)
        else:
            keys[role] = new_key()
            created.append(role)
    settings_written = set_env_values(
        env_path, dict(MONAD_TESTNET_SETTINGS), replace=False, header="Monad testnet (scripts/testnet.py keys)"
    )
    if created:
        set_env_values(
            env_path,
            {KEY_VARS[role]: keys[role] for role in created},
            replace=False,
            header="Testnet-only wallets: never reuse them, never commit this file",
        )
    return KeysResult(
        addresses={role: Account.from_key(keys[role]).address for role in ROLES},
        created=created,
        kept=kept,
        settings_written=settings_written,
        chain_mode=parse_env_file(env_path).get("CHAIN_MODE") or None,
    )


def set_chain_mode(env_path: Path, mode: str) -> str:
    value = CHAIN_MODES[mode]
    set_env_values(env_path, {"CHAIN_MODE": value}, replace=True)
    return value


# ---------------------------------------------------------------------------------------------
# Funding plan
# ---------------------------------------------------------------------------------------------


@dataclass
class Transfer:
    role: str
    to: str
    amount: int


@dataclass
class FundingPlan:
    price: int  # charged wei per gas (base + priority)
    bid: int  # max fee per gas the backend signs with (2 x base + priority): what the node checks up front
    deployed: bool
    requested_runs: int
    funded_runs: int  # runs the plan covers (>= requested_runs when nothing is missing)
    deployer_keep: int  # left on the deployer for the deployment (if pending) and a top-up
    transfers: list[Transfer] = field(default_factory=list)
    missing: int = 0  # MON (wei) to add to the deployer to cover requested_runs
    targets: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(t.amount for t in self.transfers)


def _targets(runs: int, price: int) -> dict[str, int]:
    # runs + 1: the extra run's worth covers the up-front check at the max fee (about 2 x the charged price)
    return {
        "authorizer": (runs + 1) * RUN_GAS_LIMIT["authorizer"] * price,
        "executor": (runs + 1) * RUN_GAS_LIMIT["executor"] * price,
        "pauser": PAUSER_GAS * price,
    }


def plan_funding(
    balances: Mapping[str, int],
    addresses: Mapping[str, str],
    *,
    price: int,
    bid: int,
    deployed: bool,
    runs: int = DEFAULT_RUNS,
) -> FundingPlan:
    """What the deployer should send so that the others can do ``runs`` executed runs.

    Keeps 1.5 x the deployment cost on the deployer while the contracts are not deployed (the same margin
    preflight asks for), plus one gate top-up. When the deployer cannot cover ``runs``, plans the largest
    number of runs it can cover and reports what is missing.
    """
    keep = (DEPLOY_GAS_LIMIT * price * 3 // 2 if not deployed else 0) + DEPLOYER_UPKEEP_GAS * price
    minimum_transfer = 2 * TRANSFER_GAS_LIMIT * bid  # below this a top-up costs more than it is worth

    def needs(r: int) -> tuple[list[Transfer], int]:
        out = []
        for role, target in _targets(r, price).items():
            short = target - int(balances.get(role, 0))
            if short > minimum_transfer:
                out.append(Transfer(role, addresses[role], short))
        fees = len(out) * TRANSFER_GAS_LIMIT * bid
        return out, sum(t.amount for t in out) + fees

    available = int(balances.get("deployer", 0)) - keep
    transfers, cost = needs(runs)
    missing = max(0, cost - available)
    plan = FundingPlan(price=price, bid=bid, deployed=deployed, requested_runs=runs, funded_runs=runs, deployer_keep=keep, missing=missing)
    plan.targets = _targets(runs, price)
    if missing == 0:
        plan.transfers = transfers
        return plan
    best = -1
    for r in range(runs - 1, -1, -1):
        transfers, cost = needs(r)
        if cost <= available:
            best = r
            break
    if best >= 1:
        plan.funded_runs = best
        plan.transfers = transfers
        plan.targets = _targets(best, price)
    else:
        plan.funded_runs = 0
        plan.transfers = []
    return plan


def runs_affordable(balance: int, role: str, price: int) -> int:
    per_run = RUN_GAS_LIMIT[role] * price
    return int(balance) // per_run if per_run else 0


# ---------------------------------------------------------------------------------------------
# Chain
# ---------------------------------------------------------------------------------------------


def balances_of(w3: Web3, addresses: Mapping[str, str]) -> dict[str, int]:
    return {role: int(w3.eth.get_balance(Web3.to_checksum_address(address))) for role, address in addresses.items()}


def fees(w3: Web3) -> tuple[int, int]:
    """(charged price, max fee the backend signs with) in wei per gas."""
    base, priority, charged = price_per_gas(w3)
    return charged, 2 * base + priority


def wait_for_blocks(w3: Web3, after_block: int, blocks: int, *, timeout: float = 30.0, poll: float = 0.3) -> None:
    deadline = time.monotonic() + timeout
    while int(w3.eth.block_number) < after_block + blocks and time.monotonic() < deadline:
        time.sleep(poll)


def send_transfers(
    w3: Web3,
    chain_id: int,
    deployer_key: str,
    transfers: list[Transfer],
    *,
    out: Callable[[str], None] = print,
    blocks_between: int = RESERVE_BALANCE_BLOCKS + 1,
    tx_timeout: float = 120.0,
) -> list[tuple[Transfer, object]]:
    """Sends each transfer from the deployer, one at a time, waiting ``blocks_between`` blocks after each."""
    from ..blockchain.web3_gateway import TransactionSender, as_account

    account = as_account(deployer_key)
    sender = TransactionSender(w3, chain_id, tx_timeout=tx_timeout, poll_interval=0.5)
    results: list[tuple[Transfer, object]] = []
    for i, transfer in enumerate(transfers):
        outcome = sender.send(account, to=transfer.to, data=b"", value=transfer.amount)
        results.append((transfer, outcome))
        if not outcome.ok:
            out(f"  FAILED  {transfer.role:<10} {mon(transfer.amount)}  {outcome.error or outcome.status.value}")
            break
        out(f"  sent    {transfer.role:<10} {mon(transfer.amount)}  tx {outcome.tx_hash}")
        if blocks_between and i < len(transfers) - 1:
            wait_for_blocks(w3, int(outcome.receipt["blockNumber"]), blocks_between)
    return results
