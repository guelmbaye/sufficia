"""Read-only readiness checks for a Monad deployment (``scripts/preflight.py``).

Nothing here signs or sends a transaction. Each check returns OK, WARN or FAIL with a sentence that
says what to do. Costs follow Monad's fee model: the *gas limit* is charged (not the gas used), at
``min(base fee + priority fee, max fee)`` per gas; the backend sets limits at 1.25 x the estimate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Optional

from eth_account import Account
from web3 import Web3

from ..blockchain.artifacts import contract_abi
from ..blockchain.deploy import Deployment

# Gas *limits* the backend sets (1.25 x the estimate), measured on the real bytecode (LOCAL_EVM).
DEPLOY_GAS_LIMIT = 5_100_000  # dUSDC + TOKEN-X + router + rate + reserve + EvidenceGate + gate funding
RUN_GAS_LIMIT = {
    "authorizer": 340_000,  # registerIntent + authorize + recordReceipt, per executed run
    "executor": 180_000,  # executeAuthorized (approve + swap through the router)
}
DEMO_RUN_USDC = 100  # one Grand Prize execution spends 100 dUSDC from the gate
MAX_CLOCK_SKEW_OK = 2.0  # the engine tolerates 2 s; authority windows are whole seconds onchain
MAX_CLOCK_SKEW_WARN = 5.0

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass
class Check:
    level: str
    name: str
    detail: str


def _mon(wei: int | Decimal) -> str:
    return f"{Decimal(int(wei)) / Decimal(10**18):.4f} MON"


def price_per_gas(w3: Web3) -> tuple[int, int, int]:
    """(base fee, priority fee, charged price) in wei per gas."""
    block = w3.eth.get_block("latest")
    base = int(block.get("baseFeePerGas") or w3.eth.gas_price)
    try:
        priority = int(w3.eth.max_priority_fee)
    except Exception:  # noqa: BLE001 - some RPCs do not expose it; the backend falls back to 1 gwei
        priority = 10**9
    return base, priority, base + priority


def check_chain(w3: Web3, expected_chain_id: Optional[int], *, now: Optional[float] = None, check_clock: bool = True) -> list[Check]:
    out: list[Check] = []
    chain_id = int(w3.eth.chain_id)
    if expected_chain_id is not None and chain_id != expected_chain_id:
        out.append(Check(FAIL, "chain id", f"the RPC serves chain {chain_id}, expected {expected_chain_id}"))
    else:
        out.append(Check(OK, "chain id", str(chain_id)))
    block = w3.eth.get_block("latest")
    out.append(Check(OK, "latest block", f"{int(block['number'])}"))
    if check_clock:
        skew = (time.time() if now is None else now) - int(block["timestamp"])
        if abs(skew) <= MAX_CLOCK_SKEW_OK:
            out.append(Check(OK, "clock", f"this computer is {skew:+.1f} s from the chain"))
        else:
            level = WARN if abs(skew) <= MAX_CLOCK_SKEW_WARN else FAIL
            out.append(
                Check(
                    level,
                    "clock",
                    f"this computer is {skew:+.1f} s from the chain: evidence freshness and authority windows are "
                    "counted in seconds, so synchronize the system clock (Windows: w32tm /resync)",
                )
            )
    base, priority, charged = price_per_gas(w3)
    out.append(Check(OK, "gas price", f"base {base / 1e9:.1f} gwei + priority {priority / 1e9:.1f} gwei (charged on the gas limit)"))
    return out


def signer_addresses(keys: Mapping[str, Optional[str]]) -> tuple[dict[str, str], list[Check]]:
    addresses: dict[str, str] = {}
    out: list[Check] = []
    for role in ("deployer", "authorizer", "executor", "pauser"):
        key = keys.get(role)
        if not key:
            continue
        try:
            addresses[role] = Account.from_key(key).address
        except Exception:  # noqa: BLE001 - never echo key material
            out.append(Check(FAIL, f"{role} key", f"{role.upper()}_PRIVATE_KEY is not a valid private key"))
    for role in ("authorizer", "executor"):
        if role not in addresses and not any(c.name == f"{role} key" for c in out):
            out.append(Check(FAIL, f"{role} key", f"set {role.upper()}_PRIVATE_KEY (the backend signs with it)"))
    if addresses.get("authorizer") and addresses.get("authorizer") == addresses.get("executor"):
        out.append(Check(WARN, "separate keys", "authorizer and executor are the same address; separate keys are recommended"))
    for role, address in addresses.items():
        out.append(Check(OK, f"{role} address", address))
    return addresses, out


def check_balances(w3: Web3, addresses: Mapping[str, str], *, deployed: bool) -> list[Check]:
    out: list[Check] = []
    _, _, price = price_per_gas(w3)
    deployer = addresses.get("deployer")
    if not deployed:
        need = DEPLOY_GAS_LIMIT * price
        if not deployer:
            out.append(Check(FAIL, "deployer", "set DEPLOYER_PRIVATE_KEY to deploy (or pass --deployment for an existing one)"))
        else:
            have = int(w3.eth.get_balance(deployer))
            level = OK if have >= need * 3 // 2 else FAIL if have < need else WARN
            out.append(Check(level, "deployer balance", f"{_mon(have)}; the deployment needs about {_mon(need)} at today's gas price"))
    for role, gas in RUN_GAS_LIMIT.items():
        address = addresses.get(role)
        if not address:
            continue
        have = int(w3.eth.get_balance(address))
        per_run = gas * price
        runs = have // per_run if per_run else 0
        level = OK if runs >= 10 else WARN if runs >= 1 else FAIL
        out.append(Check(level, f"{role} balance", f"{_mon(have)}, enough for about {runs} executed runs ({_mon(per_run)} each)"))
    return out


def check_deployment(w3: Web3, deployment: Deployment, addresses: Mapping[str, str]) -> list[Check]:
    out: list[Check] = []
    chain_id = int(w3.eth.chain_id)
    if deployment.chain_id != chain_id:
        return [Check(FAIL, "deployment", f"the file is for chain {deployment.chain_id}, the RPC serves chain {chain_id}")]
    for label, address in (("EvidenceGate", deployment.gate), ("USDC", deployment.usdc), ("TOKEN-X", deployment.token_x), ("router", deployment.router)):
        has_code = len(w3.eth.get_code(Web3.to_checksum_address(address))) > 0
        out.append(Check(OK if has_code else FAIL, f"{label} code", address if has_code else f"no contract at {address} (testnet reset? deploy again)"))
    if any(c.level == FAIL for c in out):
        return out

    gate = w3.eth.contract(address=Web3.to_checksum_address(deployment.gate), abi=contract_abi("EvidenceGate"))
    authorizer = addresses.get("authorizer")
    if authorizer:
        role = gate.functions.AUTHORIZER_ROLE().call()
        granted = gate.functions.hasRole(role, authorizer).call()
        out.append(
            Check(
                OK if granted else FAIL,
                "authorizer role",
                f"{authorizer} holds AUTHORIZER_ROLE"
                if granted
                else f"{authorizer} lacks AUTHORIZER_ROLE: AUTHORIZER_PRIVATE_KEY does not match this deployment ({deployment.authorizer})",
            )
        )
    executor = addresses.get("executor")
    if executor and executor.lower() != deployment.executor.lower():
        out.append(Check(WARN, "executor", f"EXECUTOR_PRIVATE_KEY is {executor}, the deployment recorded {deployment.executor}; the key wins"))
    paused = gate.functions.paused().call()
    out.append(Check(FAIL if paused else OK, "gate paused", "paused: unpause it with the pauser key" if paused else "not paused"))
    for label, target in (("router", deployment.router), ("USDC", deployment.usdc)):
        allowed = gate.functions.allowedTargets(Web3.to_checksum_address(target)).call()
        out.append(Check(OK if allowed else FAIL, f"{label} allow-listed", "yes" if allowed else f"{target} is not an allowed target"))

    erc20 = contract_abi("DemoERC20")
    usdc = w3.eth.contract(address=Web3.to_checksum_address(deployment.usdc), abi=erc20)
    treasury = int(usdc.functions.balanceOf(Web3.to_checksum_address(deployment.gate)).call())
    unit = 10 ** int(deployment.usdc_decimals)
    runs = treasury // (DEMO_RUN_USDC * unit)
    level = OK if runs >= 10 else WARN if runs >= 1 else FAIL
    out.append(
        Check(
            level,
            "gate treasury",
            f"{Decimal(treasury) / unit:,} USDC, enough for {runs} executions of {DEMO_RUN_USDC} USDC"
            + ("" if runs >= 10 else "; top it up: python scripts/fund_gate.py --amount 10000"),
        )
    )
    router = w3.eth.contract(address=Web3.to_checksum_address(deployment.router), abi=contract_abi("DemoSwapRouter"))
    quote = int(router.functions.quote(Web3.to_checksum_address(deployment.usdc), Web3.to_checksum_address(deployment.token_x), DEMO_RUN_USDC * unit).call())
    token = w3.eth.contract(address=Web3.to_checksum_address(deployment.token_x), abi=erc20)
    reserve = int(token.functions.balanceOf(Web3.to_checksum_address(deployment.router)).call())
    swaps = reserve // quote if quote else 0
    out.append(Check(OK if swaps >= 10 else WARN if swaps >= 1 else FAIL, "router reserve", f"TOKEN-X for about {swaps} swaps of {DEMO_RUN_USDC} USDC"))
    return out


def run_checks(
    w3: Web3,
    *,
    expected_chain_id: Optional[int],
    keys: Mapping[str, Optional[str]],
    deployment: Optional[Deployment],
    now: Optional[float] = None,
    check_clock: bool = True,
) -> list[Check]:
    checks = check_chain(w3, expected_chain_id, now=now, check_clock=check_clock)
    if any(c.level == FAIL and c.name == "chain id" for c in checks):
        return checks
    addresses, signer_checks = signer_addresses(keys)
    checks += signer_checks
    checks += check_balances(w3, addresses, deployed=deployment is not None)
    if deployment is not None:
        checks += check_deployment(w3, deployment, addresses)
    return checks


def summarize(checks: list[Check]) -> dict[str, Any]:
    return {level: sum(1 for c in checks if c.level == level) for level in (OK, WARN, FAIL)}
