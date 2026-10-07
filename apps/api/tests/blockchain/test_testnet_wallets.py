"""scripts/testnet.py: wallets written to .env, funding plan, distribution from the deployer (in-process EVM)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from eth_account import Account
from web3 import Web3

from sufficia.config import LOCAL_DEV_KEYS
from sufficia.devtools.envfile import parse_env_file
from sufficia.devtools.preflight import DEPLOY_GAS_LIMIT, RUN_GAS_LIMIT
from sufficia.devtools.wallets import (
    DEPLOYER_UPKEEP_GAS,
    KEY_VARS,
    ROLES,
    TRANSFER_GAS_LIMIT,
    create_keys,
    plan_funding,
    set_chain_mode,
)

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
GWEI = 10**9
ADDRESSES = {role: Account.create().address for role in ROLES}


def _script():
    spec = importlib.util.spec_from_file_location("testnet_script", SCRIPTS / "testnet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clean_env(monkeypatch):
    for name in [*KEY_VARS.values(), "CHAIN_MODE", "DEPLOYMENT_FILE", "MONAD_RPC_URL", "MONAD_RPC_FALLBACK_URL", "MONAD_CHAIN_ID", "MONAD_EXPLORER_URL"]:
        monkeypatch.delenv(name, raising=False)


# -- .env ----------------------------------------------------------------------------------------


def test_keys_are_created_once_and_never_replaced(tmp_path, clean_env):
    env = tmp_path / ".env"
    env.write_text("API_KEY=secret   # mine\nAUTHORIZER_PRIVATE_KEY=\nCHAIN_MODE=LOCAL_EVM\n", encoding="utf-8")
    first = create_keys(env)
    assert sorted(first.created) == sorted(ROLES) and first.kept == []
    values = parse_env_file(env)
    assert values["API_KEY"] == "secret" and values["CHAIN_MODE"] == "LOCAL_EVM"  # untouched
    assert values["MONAD_CHAIN_ID"] == "10143" and values["DEPLOYMENT_FILE"] == "../../deployments/monad-testnet.json"
    for role in ROLES:
        assert Account.from_key(values[KEY_VARS[role]]).address == first.addresses[role]
    assert len(set(first.addresses.values())) == 4
    assert env.read_text(encoding="utf-8").count("AUTHORIZER_PRIVATE_KEY=") == 1  # the empty line was filled in place
    assert first.chain_mode == "LOCAL_EVM"

    second = create_keys(env)
    assert second.created == [] and sorted(second.kept) == sorted(ROLES)
    assert second.addresses == first.addresses and second.settings_written == []


def test_an_invalid_key_stops_without_writing(tmp_path, clean_env):
    env = tmp_path / ".env"
    env.write_text("DEPLOYER_PRIVATE_KEY=0x1234\n", encoding="utf-8")
    with pytest.raises(ValueError, match="DEPLOYER_PRIVATE_KEY"):
        create_keys(env)
    assert env.read_text(encoding="utf-8") == "DEPLOYER_PRIVATE_KEY=0x1234\n"


def test_chain_mode_switch(tmp_path, clean_env):
    env = tmp_path / ".env"
    env.write_text("# x\nCHAIN_MODE=LOCAL_EVM\nOTHER=1\n", encoding="utf-8")
    assert set_chain_mode(env, "monad") == "MONAD_TESTNET"
    assert parse_env_file(env) == {"CHAIN_MODE": "MONAD_TESTNET", "OTHER": "1"}
    set_chain_mode(env, "local")
    assert parse_env_file(env)["CHAIN_MODE"] == "LOCAL_EVM"
    fresh = tmp_path / "fresh.env"
    set_chain_mode(fresh, "monad")
    assert parse_env_file(fresh) == {"CHAIN_MODE": "MONAD_TESTNET"}


# -- plan ----------------------------------------------------------------------------------------


def _cost(runs: int, price: int, bid: int) -> int:
    need = (runs + 1) * (RUN_GAS_LIMIT["authorizer"] + RUN_GAS_LIMIT["executor"]) * price + 200_000 * price
    return need + 3 * TRANSFER_GAS_LIMIT * bid


def test_plan_keeps_the_deployment_cost_and_scales_down():
    price, bid = 102 * GWEI, 202 * GWEI
    keep = DEPLOY_GAS_LIMIT * price * 3 // 2 + DEPLOYER_UPKEEP_GAS * price
    empty = plan_funding({r: 0 for r in ROLES}, ADDRESSES, price=price, bid=bid, deployed=False)
    assert empty.funded_runs == 0 and empty.transfers == []
    assert empty.missing == keep + _cost(20, price, bid)
    assert 1.8e18 < empty.missing < 2.1e18  # about 2 MON at the minimum base fee

    exact = plan_funding({"deployer": empty.missing}, ADDRESSES, price=price, bid=bid, deployed=False)
    assert exact.funded_runs == 20 and exact.missing == 0 and len(exact.transfers) == 3
    assert exact.total + 3 * TRANSFER_GAS_LIMIT * bid + keep == empty.missing

    one_mon = plan_funding({"deployer": 10**18}, ADDRESSES, price=price, bid=bid, deployed=False)
    assert 1 <= one_mon.funded_runs < 20 and one_mon.missing > 0
    assert one_mon.total + 3 * TRANSFER_GAS_LIMIT * bid <= 10**18 - keep

    after_deploy = plan_funding({"deployer": 2 * 10**18}, ADDRESSES, price=price, bid=bid, deployed=True)
    assert after_deploy.funded_runs == 20 and after_deploy.deployer_keep == DEPLOYER_UPKEEP_GAS * price

    funded = {r: 10**18 for r in ROLES}
    assert plan_funding(funded, ADDRESSES, price=price, bid=bid, deployed=True).transfers == []


# -- script against the in-process EVM ----------------------------------------------------------------


@pytest.mark.evm
def test_status_then_distribute_from_the_deployer(tmp_path, clean_env):
    from sufficia.blockchain.local_evm import LocalEvmChain

    chain = LocalEvmChain(deploy=False)
    module = _script()
    module.ENV_PATH = tmp_path / ".env"
    module.ENV_PATH.write_text(f"MONAD_CHAIN_ID={chain.chain_id}\nDEPLOYMENT_FILE={tmp_path / 'none.json'}\n", encoding="utf-8")

    lines: list[str] = []
    assert module.main(["status"], out=lines.append, w3=chain.w3) == 2
    assert "python scripts/testnet.py keys" in lines[-1]

    lines.clear()
    assert module.main(["keys"], out=lines.append) == 0
    deployer = Account.from_key(parse_env_file(module.ENV_PATH)[KEY_VARS["deployer"]]).address
    assert any(deployer in line for line in lines) and not any("PRIVATE_KEY=" in line for line in lines)

    lines.clear()
    assert module.main(["status"], out=lines.append, w3=chain.w3) == 1  # nothing on the deployer yet
    assert any("DEPLOYER" in line for line in lines) and any(deployer in line for line in lines)

    from sufficia.blockchain.web3_gateway import TransactionSender

    funder = Account.from_key(LOCAL_DEV_KEYS["deployer"])
    assert TransactionSender(chain.w3, chain.chain_id).send(funder, to=deployer, data=b"", value=Web3.to_wei(1, "ether")).ok

    lines.clear()
    assert module.main(["status"], out=lines.append, w3=chain.w3) == 0
    assert any("python scripts/testnet.py distribute" in line for line in lines), lines

    lines.clear()
    assert module.main(["distribute", "--dry-run"], out=lines.append, w3=chain.w3) == 0
    assert lines[-1] == "(dry run: nothing sent)"

    lines.clear()
    assert module.main(["distribute"], out=lines.append, w3=chain.w3, blocks_between=0) == 0, lines
    assert sum(1 for line in lines if line.strip().startswith("sent")) == 3
    env = parse_env_file(module.ENV_PATH)
    price = int(chain.w3.eth.get_block("latest")["baseFeePerGas"]) + int(chain.w3.eth.max_priority_fee)
    for role in ("authorizer", "executor"):
        balance = chain.w3.eth.get_balance(Account.from_key(env[KEY_VARS[role]]).address)
        assert balance // (RUN_GAS_LIMIT[role] * price) >= 20

    lines.clear()
    assert module.main(["distribute"], out=lines.append, w3=chain.w3) == 0
    assert lines[-1].startswith("Nothing to send")

    lines.clear()
    assert module.main(["status"], out=lines.append, w3=chain.w3) == 0
    assert lines[-2:] == ["Next: python scripts/preflight.py --no-deployment", "      python scripts/deploy.py --network monad-testnet"]
