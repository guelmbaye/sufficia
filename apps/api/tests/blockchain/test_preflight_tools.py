"""scripts/preflight.py checks and scripts/fund_gate.py, against the real bytecode on the in-process EVM."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from eth_account import Account

from sufficia.config import LOCAL_DEV_KEYS
from sufficia.devtools.envfile import parse_env_file
from sufficia.devtools.preflight import FAIL, OK, run_checks

pytestmark = pytest.mark.evm
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture(scope="module")
def chain():
    from sufficia.blockchain.local_evm import LocalEvmChain

    return LocalEvmChain()


def _keys(**override):
    keys = {role: LOCAL_DEV_KEYS[role] for role in ("deployer", "authorizer", "executor", "pauser")}
    keys.update(override)
    return keys


def _by_name(checks):
    return {c.name: c for c in checks}


def test_a_correct_deployment_passes_every_check(chain):
    checks = run_checks(chain.w3, expected_chain_id=chain.chain_id, keys=_keys(), deployment=chain.deployment, check_clock=False)
    assert [c for c in checks if c.level == FAIL] == []
    named = _by_name(checks)
    for name in ("authorizer role", "gate paused", "router allow-listed", "USDC allow-listed", "gate treasury", "router reserve"):
        assert named[name].level == OK, (name, named[name])
    assert "10 executions of 100 USDC" in named["gate treasury"].detail  # the default 1,000 dUSDC


def test_a_key_that_does_not_hold_the_role_fails(chain):
    stranger = Account.create().key.hex()
    named = _by_name(run_checks(chain.w3, expected_chain_id=chain.chain_id, keys=_keys(authorizer=stranger), deployment=chain.deployment, check_clock=False))
    assert named["authorizer role"].level == FAIL and "lacks AUTHORIZER_ROLE" in named["authorizer role"].detail


def test_missing_backend_keys_and_wrong_chain_fail(chain):
    named = _by_name(run_checks(chain.w3, expected_chain_id=chain.chain_id, keys=_keys(executor=None), deployment=None, check_clock=False))
    assert named["executor key"].level == FAIL
    named = _by_name(run_checks(chain.w3, expected_chain_id=10143, keys=_keys(), deployment=None, check_clock=False))
    assert named["chain id"].level == FAIL and "expected 10143" in named["chain id"].detail


def test_clock_skew_is_reported(chain):
    block = chain.w3.eth.get_block("latest")
    named = _by_name(run_checks(chain.w3, expected_chain_id=None, keys=_keys(), deployment=None, now=int(block["timestamp"]) + 30))
    assert named["clock"].level == FAIL and "w32tm /resync" in named["clock"].detail
    named = _by_name(run_checks(chain.w3, expected_chain_id=None, keys=_keys(), deployment=None, now=int(block["timestamp"]) + 1))
    assert named["clock"].level == OK


def test_fund_gate_mints_demo_usdc_to_the_gate(chain, tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("fund_gate_script", SCRIPTS / "fund_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path / "local.json"
    chain.deployment.save(target)
    monkeypatch.setenv("DEPLOYER_PRIVATE_KEY", LOCAL_DEV_KEYS["deployer"])
    lines: list[str] = []
    assert module.main(["--amount", "2500", "--deployment", str(target)], out=lines.append, w3=chain.w3) == 0, lines
    assert lines[-1].endswith("-> 3,500 dUSDC") or "3,500" in lines[-1], lines
    named = _by_name(run_checks(chain.w3, expected_chain_id=chain.chain_id, keys=_keys(), deployment=chain.deployment, check_clock=False))
    assert "35 executions" in named["gate treasury"].detail


def test_env_file_parsing(tmp_path):
    f = tmp_path / ".env"
    f.write_text(
        "# comment\nCHAIN_MODE=MONAD_TESTNET   # inline comment\nexport A=1\nB=\"quoted # not a comment\"\nEMPTY=\nURL=https://x/y#frag\n",
        encoding="utf-8",
    )
    assert parse_env_file(f) == {"CHAIN_MODE": "MONAD_TESTNET", "A": "1", "B": "quoted # not a comment", "EMPTY": "", "URL": "https://x/y#frag"}
