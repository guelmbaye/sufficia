"""deploy_suite, the deploy CLI (local network only) and the gateway factory's configuration errors.

No test here talks to a remote RPC: Monad-mode gateways are built offline (construction makes no network
call) and, where connectivity is exercised, they point at a closed localhost port.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from sufficia.blockchain.artifacts import contract_abi
from sufficia.blockchain.deploy import (
    MONAD_MAINNET_EXPLORER,
    Deployment,
    chain_info_from_settings,
    deploy_suite,
    load_deployment,
)
from sufficia.blockchain.factory import build_gateway, get_local_chain, reset_local_chain
from sufficia.blockchain.web3_gateway import ConfigurationError, FailoverProvider, Web3Gateway
from sufficia.config import LOCAL_DEV_KEYS, Settings
from sufficia.domain.enums import ChainMode, TxStatus

API_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = API_ROOT / "scripts" / "deploy.py"
CLOSED_RPC = "http://127.0.0.1:9"  # nothing listens there: connection refused, instantly, offline
AUTH_KEY = "0x" + "a1" * 32
EXEC_KEY = "0x" + "b2" * 32

ENV_VARS = (
    "CHAIN_MODE", "DEPLOYMENT_FILE", "EVIDENCE_GATE_ADDRESS", "USDC_ADDRESS", "TOKEN_X_ADDRESS", "SWAP_ROUTER_ADDRESS",
    "DEPLOYER_PRIVATE_KEY", "AUTHORIZER_PRIVATE_KEY", "EXECUTOR_PRIVATE_KEY", "PAUSER_PRIVATE_KEY", "MONAD_RPC_URL",
    "MONAD_RPC_FALLBACK_URL", "MONAD_CHAIN_ID", "MONAD_EXPLORER_URL", "EXECUTION_ADAPTER", "AUTHORIZER_ADDRESS",
    "EXECUTOR", "PAUSER",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def load_script():
    spec = importlib.util.spec_from_file_location("sufficia_deploy_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def monad_settings(**overrides) -> Settings:
    values = dict(
        _env_file=None,
        chain_mode="monad-testnet",
        monad_rpc_url=CLOSED_RPC,
        monad_rpc_fallback_url="http://127.0.0.1:10",
        authorizer_private_key=AUTH_KEY,
        executor_private_key=EXEC_KEY,
        tx_confirmation_timeout_seconds=1.0,
        http_timeout_seconds=1.0,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(scope="module")
def deployment_file(tmp_path_factory, local_chain) -> Path:
    path = tmp_path_factory.mktemp("deployments") / "monad-testnet.json"
    deployment = Deployment.from_dict({**local_chain.deployment.to_dict(), "chain_id": 10143, "network": "monad-testnet"})
    deployment.save(path)
    return path


# ---------------------------------------------------------------------------------------------
# deploy_suite / Deployment
# ---------------------------------------------------------------------------------------------


@pytest.mark.evm
def test_deploy_suite_result_and_roundtrip(local_chain):
    d = local_chain.deployment
    assert d.chain_id == local_chain.chain_id and d.usdc_is_demo
    assert set(d.txs) == {"deploy_usdc", "deploy_token_x", "deploy_router", "set_rate", "mint_router_reserve", "deploy_gate", "fund_gate"}
    assert d.deployer == local_chain.address("deployer") and d.authorizer == local_chain.address("authorizer")
    g = local_chain.gateway
    assert g.has_role("authorizer", d.authorizer) and g.has_role("pauser", d.pauser) and g.has_role("admin", d.deployer)
    for target in (d.router, d.usdc):
        assert g.gate.functions.allowedTargets(target).call()
    router = local_chain.w3.eth.contract(address=d.router, abi=contract_abi("DemoSwapRouter"))
    assert router.functions.rates(d.usdc, d.token_x).call() == 2_336_000_000_000_000_000
    assert router.functions.quote(d.usdc, d.token_x, 100 * 10**6).call() == 233_600_000_000_000_000_000
    usdc = local_chain.w3.eth.contract(address=d.usdc, abi=g._erc20_abi)
    assert (usdc.functions.symbol().call(), usdc.functions.name().call(), usdc.functions.decimals().call()) == ("dUSDC", "Demo USD Coin", 6)
    assert g.token_balance(d.token_x, d.router) == 1_000_000 * 10**18

    text = d.to_json()
    data = json.loads(text)
    assert data["rate_e18"] == "2336000000000000000"  # big ints as strings (JS-safe)
    assert Deployment.from_json(text) == d
    assert Deployment.from_json(data) == d


def test_deployment_json_validation(tmp_path):
    with pytest.raises(ConfigurationError, match="missing"):
        Deployment.from_json(json.dumps({"chain_id": 1}))
    with pytest.raises(ConfigurationError, match="invalid"):
        Deployment.from_json("{not json")
    with pytest.raises(ConfigurationError, match="not an address"):
        Deployment.from_dict({k: "0x" + "11" * 20 for k in ("gate", "usdc", "token_x", "router", "deployer", "authorizer", "pauser", "executor")} | {"chain_id": 1, "gate": "nope"})
    with pytest.raises(ConfigurationError, match="not found"):
        load_deployment(tmp_path / "missing.json")


@pytest.mark.evm
def test_deploy_suite_with_existing_usdc(local_chain):
    from sufficia.blockchain.local_evm import LocalEvmChain

    chain = LocalEvmChain(deploy=False)
    first = deploy_suite(chain.w3, deployer_key=LOCAL_DEV_KEYS["deployer"], authorizer=chain.address("authorizer"), pauser=chain.address("pauser"), executor=chain.address("executor"), before_tx=chain.bootstrap_clock, poll_interval=0.01)
    second = deploy_suite(chain.w3, deployer_key=LOCAL_DEV_KEYS["deployer"], authorizer=chain.address("authorizer"), pauser=chain.address("pauser"), executor=chain.address("executor"), existing_usdc=first.usdc, before_tx=chain.bootstrap_clock, poll_interval=0.01)
    assert second.usdc == first.usdc and not second.usdc_is_demo and second.gate_usdc_funding == 0
    assert "deploy_usdc" not in second.txs and "fund_gate" not in second.txs
    assert second.gate != first.gate
    with pytest.raises(ConfigurationError):
        deploy_suite(chain.w3, deployer_key=LOCAL_DEV_KEYS["deployer"], authorizer="nope", pauser=chain.address("pauser"), executor=chain.address("executor"))


# ---------------------------------------------------------------------------------------------
# scripts/deploy.py (local network only)
# ---------------------------------------------------------------------------------------------


@pytest.mark.evm
def test_deploy_script_local(tmp_path):
    script = load_script()
    out: list[str] = []
    target = tmp_path / "local.json"
    assert script.main(["--network", "local", "--out", str(target)], env={}, out=out.append) == 0
    deployment = load_deployment(target)
    text = "\n".join(out)
    for address in (deployment.gate, deployment.usdc, deployment.token_x, deployment.router):
        assert address in text
    assert f"wrote {target}" in text
    assert deployment.authorizer == script.Account.from_key(LOCAL_DEV_KEYS["authorizer"]).address
    for key in LOCAL_DEV_KEYS.values():
        assert key[2:] not in text  # private keys are never printed


@pytest.mark.evm
def test_deploy_script_role_overrides(tmp_path):
    script = load_script()
    out: list[str] = []
    env = {"AUTHORIZER_ADDRESS": "0x000000000000000000000000000000000000a11c", "EXECUTOR": "0x000000000000000000000000000000000000e0e0"}
    assert script.main(["--network", "local", "--out", str(tmp_path / "x.json")], env=env, out=out.append) == 0
    deployment = load_deployment(tmp_path / "x.json")
    assert deployment.authorizer.lower() == env["AUTHORIZER_ADDRESS"].lower()
    assert deployment.executor.lower() == env["EXECUTOR"].lower()


def test_deploy_script_configuration_errors():
    script = load_script()
    out: list[str] = []
    assert script.main(["--network", "monad-testnet"], env={}, out=out.append) == 2
    assert "DEPLOYER_PRIVATE_KEY is required" in out[-1]
    assert script.main(["--network", "monad-mainnet"], env={"DEPLOYER_PRIVATE_KEY": AUTH_KEY}, out=out.append) == 2
    assert "--yes-mainnet" in out[-1]
    assert script.main(["--network", "monad-testnet"], env={"DEPLOYER_PRIVATE_KEY": "0x1234"}, out=out.append) == 2
    assert "not a valid private key" in out[-1] and "1234" not in out[-1]
    assert script.main(["--network", "local", "--usdc", "nope"], env={}, out=out.append) == 2
    assert script.main(["--network", "local"], env={"AUTHORIZER_ADDRESS": "0x12"}, out=out.append) == 2


def test_deploy_script_unreachable_rpc_exits_3():
    script = load_script()
    out: list[str] = []
    # Roles are mandatory on Monad (no dev defaults) and are checked before any RPC call.
    env = {"DEPLOYER_PRIVATE_KEY": AUTH_KEY}
    assert script.main(["--network", "monad-testnet", "--rpc", CLOSED_RPC], env=env, out=out.append) == 2
    assert "authorizer" in out[-1]
    env.update({"AUTHORIZER_PRIVATE_KEY": AUTH_KEY, "EXECUTOR_PRIVATE_KEY": EXEC_KEY})
    code = script.main(["--network", "monad-testnet", "--rpc", CLOSED_RPC], env=env, out=out.append)
    assert code == 3 and "RPC unreachable" in out[-1]
    assert AUTH_KEY[2:] not in "\n".join(out)


@pytest.mark.evm
def test_deploy_script_cli_entrypoint(tmp_path):
    target = tmp_path / "cli.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--network", "local", "--out", str(target)],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert load_deployment(target).gate in proc.stdout


# ---------------------------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------------------------


def test_factory_disabled():
    assert build_gateway(Settings(_env_file=None, chain_mode="disabled")) is None


@pytest.mark.evm
def test_factory_local_singleton():
    reset_local_chain()
    try:
        settings = Settings(_env_file=None, chain_mode="local-evm")
        gateway = build_gateway(settings)
        assert gateway is build_gateway(settings) is get_local_chain().gateway
        assert gateway.info.mode == ChainMode.LOCAL_EVM and gateway.info.network_name == "local-evm (py-evm, not Monad)"
        assert gateway.health()["ok"]
        reset_local_chain()
        assert build_gateway(settings) is not gateway
    finally:
        reset_local_chain()


def test_factory_monad_requires_keys():
    with pytest.raises(ConfigurationError) as exc:
        build_gateway(Settings(_env_file=None, chain_mode="monad-testnet", evidence_gate_address="0x" + "11" * 20))
    assert "AUTHORIZER_PRIVATE_KEY" in str(exc.value) and "EXECUTOR_PRIVATE_KEY" in str(exc.value)
    with pytest.raises(ConfigurationError, match="EXECUTOR_PRIVATE_KEY"):
        build_gateway(Settings(_env_file=None, chain_mode="monad-mainnet", authorizer_private_key=AUTH_KEY))


def test_factory_monad_requires_addresses():
    with pytest.raises(ConfigurationError, match="no EvidenceGate address"):
        build_gateway(monad_settings())
    # demo_swap needs the whole demo suite.
    with pytest.raises(ConfigurationError, match="USDC_ADDRESS"):
        build_gateway(monad_settings(evidence_gate_address="0x" + "11" * 20))
    # transfer needs the gate + USDC only.
    gateway = build_gateway(monad_settings(evidence_gate_address="0x" + "11" * 20, usdc_address="0x" + "22" * 20, execution_adapter="transfer"))
    assert isinstance(gateway, Web3Gateway) and gateway.info.router_address is None


def test_factory_monad_invalid_values():
    with pytest.raises(ConfigurationError) as exc:
        build_gateway(monad_settings(authorizer_private_key="0xdeadbeef", evidence_gate_address="0x" + "11" * 20))
    assert "deadbeef" not in str(exc.value)
    with pytest.raises(ConfigurationError, match="not an EVM address"):
        build_gateway(monad_settings(evidence_gate_address="gate"))
    with pytest.raises(ConfigurationError, match="not found"):
        build_gateway(monad_settings(deployment_file="/nonexistent/monad.json"))


@pytest.mark.evm
def test_factory_monad_from_deployment_file_is_offline_and_fails_closed(deployment_file):
    gateway = build_gateway(monad_settings(deployment_file=str(deployment_file)))
    assert isinstance(gateway, Web3Gateway)
    info = gateway.info
    d = load_deployment(deployment_file)
    assert info.mode == ChainMode.MONAD_TESTNET and info.chain_id == 10143 and info.network_name == "monad-testnet"
    assert (info.gate_address, info.usdc_address, info.token_x_address, info.router_address) == (d.gate, d.usdc, d.token_x, d.router)
    assert info.authorizer == gateway.signer_address("authorizer") and info.executor == gateway.signer_address("executor")
    assert gateway.explorer_tx_url("0xabc") == "https://testnet.monadvision.com/tx/0xabc"
    provider = gateway.w3.provider
    assert isinstance(provider, FailoverProvider) and provider.labels == ["http://127.0.0.1:9", "http://127.0.0.1:10"]
    # Never silently local: with no reachable RPC, health reports it and writes fail closed.
    health = gateway.health()
    assert health["ok"] is False and "ChainUnavailable" in health["error"]
    result = gateway.register_intent("0x" + "ab" * 32, "0x" + "cd" * 32)
    assert result.status == TxStatus.FAILED and result.tx_hash is None


@pytest.mark.evm
def test_factory_monad_address_conflicts_and_chain_ids(deployment_file):
    d = load_deployment(deployment_file)
    with pytest.raises(ConfigurationError, match="conflicts"):
        build_gateway(monad_settings(deployment_file=str(deployment_file), evidence_gate_address="0x" + "99" * 20))
    same = build_gateway(monad_settings(deployment_file=str(deployment_file), evidence_gate_address=d.gate.lower()))
    assert same.info.gate_address == d.gate
    # A testnet deployment file cannot drive MONAD_MAINNET.
    with pytest.raises(ConfigurationError, match="143"):
        build_gateway(monad_settings(chain_mode="monad-mainnet", deployment_file=str(deployment_file)))
    # Mainnet from explicit settings: chain id 143 required; explorer never points at testnet.
    explicit = dict(evidence_gate_address=d.gate, usdc_address=d.usdc, token_x_address=d.token_x, swap_router_address=d.router)
    with pytest.raises(ConfigurationError, match="143"):
        build_gateway(monad_settings(chain_mode="monad-mainnet", **explicit))
    mainnet = build_gateway(monad_settings(chain_mode="monad-mainnet", monad_chain_id=143, **explicit))
    assert mainnet.info.chain_id == 143 and mainnet.info.explorer_url == MONAD_MAINNET_EXPLORER
    with pytest.raises(ConfigurationError):
        chain_info_from_settings(Settings(_env_file=None, chain_mode="local-evm"))
