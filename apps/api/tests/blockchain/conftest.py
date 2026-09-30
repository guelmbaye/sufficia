"""Fixtures for the blockchain integration tests (LocalEvmChain + the real backend decision path).

Everything here runs offline: py-evm in process, fake providers for failover. No real RPC is ever called.
"""

from __future__ import annotations

import secrets
import sys
import types
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import pytest

API_ROOT = Path(__file__).resolve().parents[2]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from sufficia.canonical.hashes import REQUEST_DOMAIN, RESPONSE_DOMAIN, contract_hash, raw_hash  # noqa: E402
from sufficia.config import LOCAL_DEV_KEYS, Settings  # noqa: E402
from sufficia.decision_engine import build_authorization, evaluate  # noqa: E402
from sufficia.domain.enums import ActionType, ChainMode, DecisionState, Operator, SourceMode, SourceType  # noqa: E402
from sufficia.domain.models import (  # noqa: E402
    AuthorizationPolicy,
    ContractPolicy,
    EconomicLimits,
    EvidenceClaim,
    EvidenceContract,
    EvidenceIntegrity,
    EvidenceItem,
    EvidenceSet,
    EvidenceSource,
    EvidenceTemporal,
    IntentAction,
    Money,
    Requirement,
)
from sufficia.execution import action_deadline, action_nonce, get_adapter  # noqa: E402

USDC = 10**6
TOKENX = 10**18
RECIPIENT = "0x000000000000000000000000000000000000bEEF"


# ---------------------------------------------------------------------------------------------
# Domain builders (a small contract + evidence, evaluated by the real Decision Engine)
# ---------------------------------------------------------------------------------------------


def make_contract(
    *,
    amount: str = "100",
    asset: str = "TOKEN-X",
    action_type: ActionType = ActionType.BUY,
    ttl: int = 120,
    contract_id: str = "ctr_chain_test",
    intent_id: str = "int_chain_test",
    max_age: int = 60,
) -> EvidenceContract:
    return EvidenceContract(
        contract_id=contract_id,
        intent_id=intent_id,
        action=IntentAction(type=action_type, asset=asset, max_amount=Money(value=Decimal(amount), currency="USDC")),
        requirements=[
            Requirement(id="r_inflow", claim="smart_money_inflow_24h", operator=Operator.GT, threshold=Decimal("1000000"), unit="USD", max_age_seconds=max_age),
            Requirement(id="r_liquidity", claim="available_liquidity", operator=Operator.GT, threshold=Decimal("500000"), unit="USD", max_age_seconds=max_age),
            Requirement(id="r_price", claim="asset_price", operator=Operator.GT, threshold=Decimal("0"), unit="USD", max_age_seconds=max_age, origin="IMPLICIT"),
        ],
        policy=ContractPolicy(minimum_required_evidence=3),
        economic_limits=EconomicLimits(max_execution_value_usd=Decimal("1000"), max_evidence_cost_usd=Decimal("0.25")),
        authorization=AuthorizationPolicy(ttl_seconds=ttl),
    )


def make_item(
    contract: EvidenceContract,
    requirement_id: str,
    claim: str,
    value: Any,
    observed_at: datetime,
    *,
    evidence_id: Optional[str] = None,
    provider: str = "scenario",
) -> EvidenceItem:
    evidence_id = evidence_id or f"ev_{requirement_id}_{provider}"
    return EvidenceItem(
        evidence_id=evidence_id,
        contract_id=contract.contract_id,
        requirement_id=requirement_id,
        claim=EvidenceClaim(name=claim, value=value, unit="USD"),
        temporal=EvidenceTemporal(observed_at=observed_at, received_at=observed_at),
        source=EvidenceSource(provider=provider, source_type=SourceType.API, mode=SourceMode.SIMULATED, source_family=provider),
        integrity=EvidenceIntegrity(
            request_hash=raw_hash(REQUEST_DOMAIN, f"{evidence_id}|req".encode()),
            response_hash=raw_hash(RESPONSE_DOMAIN, f"{evidence_id}|{value}".encode()),
        ),
    )


def make_evidence(
    contract: EvidenceContract,
    now: datetime,
    *,
    inflow: Any = Decimal("1340000"),
    liquidity: Any = Decimal("750000"),
    prices: tuple[Any, ...] = (Decimal("0.428"),),
    age_seconds: float = 2.0,
) -> list[EvidenceItem]:
    observed = now - timedelta(seconds=age_seconds)
    items = [
        make_item(contract, "r_inflow", "smart_money_inflow_24h", inflow, observed),
        make_item(contract, "r_liquidity", "available_liquidity", liquidity, observed),
    ]
    for i, price in enumerate(prices):
        items.append(make_item(contract, "r_price", "asset_price", price, observed, provider=f"price{i}"))
    return items


class Flow:
    """One backend flow: contract -> evidence -> Decision (real engine) -> CanonicalAction -> Authorization."""

    def __init__(self, chain: Any, settings: Settings, *, adapter: str = "demo_swap", **contract_kwargs: Any) -> None:
        self.chain = chain
        self.gateway = chain.gateway
        self.settings = settings
        self.adapter = get_adapter(adapter)
        # Unique ids per flow: identical contracts evaluated on identical evidence in the same second are
        # (deterministically) the same authorization, which the gate would reject as AuthorizationExists.
        suffix = secrets.token_hex(6)
        contract_kwargs.setdefault("contract_id", f"ctr_chain_{suffix}")
        contract_kwargs.setdefault("intent_id", f"int_chain_{suffix}")
        self.contract = make_contract(**contract_kwargs)
        self.contract_hash = contract_hash(self.contract)
        self.onchain_intent_id = raw_hash(b"TEST_INTENT", secrets.token_bytes(16))

    def decide(self, **evidence_kwargs: Any):
        # Whole-second evaluation time (the engine requires >= 1 whole second of committable authority).
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.evidence = make_evidence(self.contract, now, **evidence_kwargs)
        self.decision = evaluate(self.contract, EvidenceSet(contract_id=self.contract.contract_id, items=self.evidence), now)
        return self.decision

    def prepare(self, **overrides: Any):
        self.action = self.adapter.prepare(
            contract=self.contract,
            decision=self.decision,
            evidence_items=self.evidence,
            chain=self.gateway.info,
            nonce=overrides.pop("nonce", action_nonce(self.decision)),
            deadline=overrides.pop("deadline", action_deadline(self.decision)),
            settings=overrides.pop("settings", self.settings),
        )
        return self.action

    def authorization(self):
        self.auth = build_authorization(
            decision=self.decision,
            contract=self.contract,
            action=self.action,
            executor=self.gateway.info.executor,
            onchain_intent_id=self.onchain_intent_id,
        )
        return self.auth

    def authorize_onchain(self, **evidence_kwargs: Any):
        """decide -> prepare -> build_authorization -> register_intent -> authorize (asserting success)."""
        decision = self.decide(**evidence_kwargs)
        assert decision.state == DecisionState.AUTHORIZE, decision.summary
        self.prepare()
        self.authorization()
        registered = self.gateway.register_intent(self.onchain_intent_id, self.contract_hash)
        assert registered.ok, registered.error
        self.authorize_tx = self.gateway.authorize(self.auth)
        assert self.authorize_tx.ok, (self.authorize_tx.error_name, self.authorize_tx.error)
        return self.auth


@pytest.fixture(scope="module")
def local_chain():
    """One deployed LocalEvmChain per test module (py-evm in memory, ~1 s). Treasury: 100k demo USDC."""
    pytest.importorskip("eth_tester")
    from sufficia.blockchain.local_evm import LocalEvmChain

    return LocalEvmChain(tx_timeout=10.0, gate_usdc_funding=100_000 * USDC)


@pytest.fixture
def fresh_chain():
    """A dedicated chain for tests that mutate global state (pause, time travel, auto-mining)."""
    pytest.importorskip("eth_tester")
    from sufficia.blockchain.local_evm import LocalEvmChain

    return LocalEvmChain(tx_timeout=10.0)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        chain_mode=ChainMode.LOCAL_EVM,
        execution_adapter="demo_swap",
        execution_recipient=RECIPIENT,
        slippage_bps=100,
        tx_confirmation_timeout_seconds=10.0,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
    )


@pytest.fixture
def flow_factory(settings):
    def factory(chain: Any, **kwargs: Any) -> Flow:
        return Flow(chain, settings, **kwargs)

    return factory


@pytest.fixture(scope="session")
def helpers() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        make_contract=make_contract,
        make_evidence=make_evidence,
        make_item=make_item,
        Flow=Flow,
        USDC=USDC,
        TOKENX=TOKENX,
        RECIPIENT=RECIPIENT,
        LOCAL_DEV_KEYS=LOCAL_DEV_KEYS,
    )
