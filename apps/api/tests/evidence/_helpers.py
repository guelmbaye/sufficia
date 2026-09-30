"""Shared helpers for the evidence / x402 tests (loaded by path from the evidence and x402 conftests).

No network: HTTP goes through httpx.MockTransport or httpx.ASGITransport, EVM reads go through an
in-memory web3 provider. Tests reach these helpers through the ``h`` fixture.
"""

from __future__ import annotations

import asyncio
import json
import types
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

import httpx
from eth_abi import encode as abi_encode
from web3 import Web3
from web3.providers.base import BaseProvider

from sufficia.config import Settings
from sufficia.domain.enums import ActionType, ChainMode, Operator, SourceMode, SourceType
from sufficia.domain.models import (
    EconomicLimits,
    EvidenceContract,
    IntentAction,
    Money,
    Requirement,
)
from sufficia.evidence.base import AcquisitionContext, EvidenceBudget, RawEvidence


class _TestSettings(Settings):
    """Settings plus the optional knobs the evidence layer reads through getattr()."""

    nansen_x402: bool = False
    nansen_x402_network: Optional[str] = None
    nansen_x402_asset: Optional[str] = None
    x402_send_retries: int = 1
    x402_evidence_claims: Optional[str] = None


TOKEN = "0x1111111111111111111111111111111111111111"
FEED = "0x2222222222222222222222222222222222222222"
POOL = "0x3333333333333333333333333333333333333333"


def make_settings(**overrides: Any) -> _TestSettings:
    base: dict[str, Any] = dict(
        _env_file=None,
        chain_mode=ChainMode.LOCAL_EVM,
        evidence_profile="demo",
        evidence_sources_json=None,
        http_timeout_seconds=2.0,
        asset_token_address=None,
        nansen_api_key=None,
        nansen_base_url="https://nansen.test",
        dexscreener_base_url="https://dexscreener.test",
        dexscreener_pair_address=None,
        chainlink_feed_address=None,
        chainlink_rpc_url=None,
        monad_rpc_url="http://rpc.test",
        pool_address=None,
        x402_enabled=True,
        x402_evidence_url=None,
        x402_pay_to_allowlist="",
        evidence_payment_private_key=None,
        x402_reference_enabled=True,
    )
    base.update(overrides)
    return _TestSettings(**base)


def make_contract(*requirements: Requirement, asset: str = "TOKEN-X", budget: str = "0.25", minimum: int = 0) -> EvidenceContract:
    reqs = list(requirements) or [
        Requirement(id="r_flow", claim="smart_money_inflow_24h", operator=Operator.GT, threshold=Decimal("1000000")),
        Requirement(id="r_liq", claim="available_liquidity", operator=Operator.GTE, threshold=Decimal("50000")),
        Requirement(id="r_price", claim="asset_price", operator=Operator.GT, threshold=Decimal("0")),
    ]
    from sufficia.domain.models import ContractPolicy

    return EvidenceContract(
        contract_id="ctr_test",
        intent_id="int_test",
        action=IntentAction(type=ActionType.BUY, asset=asset, max_amount=Money(value=Decimal("100"))),
        requirements=reqs,
        policy=ContractPolicy(minimum_required_evidence=minimum),
        economic_limits=EconomicLimits(max_execution_value_usd=Decimal("1000"), max_evidence_cost_usd=Decimal(budget)),
    )


def req(claim: str, rid: Optional[str] = None, **kw: Any) -> Requirement:
    kw.setdefault("operator", Operator.EXISTS)
    return Requirement(id=rid or f"r_{claim}", claim=claim, **kw)


def make_ctx(
    contract: Optional[EvidenceContract] = None,
    settings: Optional[Settings] = None,
    *,
    http: Optional[httpx.AsyncClient] = None,
    reference_http: Optional[httpx.AsyncClient] = None,
    budget: Optional[EvidenceBudget] = None,
    run_id: str = "run_test",
    now: Optional[Callable[[], datetime]] = None,
) -> AcquisitionContext:
    contract = contract or make_contract()
    settings = settings or make_settings()
    kwargs: dict[str, Any] = {}
    if now is not None:
        kwargs["now"] = now
    return AcquisitionContext(
        contract=contract,
        run_id=run_id,
        settings=settings,
        budget=budget or EvidenceBudget(Decimal("0.25"), Decimal("0.10")),
        http=http or httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(599))),
        reference_http=reference_http,
        **kwargs,
    )


def mock_http(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def json_response(status: int, data: Any, headers: Optional[dict[str, str]] = None) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(data).encode(), headers={"content-type": "application/json", **(headers or {})})


def raw_evidence(**kw: Any) -> RawEvidence:
    base: dict[str, Any] = dict(
        provider="test-provider",
        source_type=SourceType.API,
        mode=SourceMode.LIVE,
        claim="available_liquidity",
        value=Decimal("600000"),
        unit="USD",
        observed_at=datetime.now(timezone.utc),
        received_at=datetime.now(timezone.utc),
        request_descriptor={"method": "GET", "url": "https://example.test/x"},
        response_body=b'{"x":1}',
        source_family="test",
    )
    base.update(kw)
    return RawEvidence(**base)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---- in-memory EVM --------------------------------------------------------------------------------


class FakeEVMProvider(BaseProvider):
    """Answers the handful of JSON-RPC methods the ONCHAIN/ORACLE adapters use."""

    def __init__(self, *, chain_id: int = 10143, block_number: int = 1234, block_hash: str = "0x" + "ab" * 32, timestamp: int = 1_790_000_000):
        super().__init__()
        self.chain_id = chain_id
        self.block_number = block_number
        self.block_hash = block_hash
        self.timestamp = timestamp
        self.calls: dict[str, bytes | Exception] = {}  # selector -> return data / exception
        self.requests: list[tuple[str, Any]] = []
        self.fail_with: Optional[Exception] = None

    def make_request(self, method: Any, params: Any) -> Any:
        self.requests.append((method, params))
        if self.fail_with is not None:
            raise self.fail_with
        if method == "eth_chainId":
            return {"jsonrpc": "2.0", "id": 1, "result": hex(self.chain_id)}
        if method == "eth_getBlockByNumber":
            return {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"number": hex(self.block_number), "hash": self.block_hash, "timestamp": hex(self.timestamp)},
            }
        if method == "eth_call":
            data = params[0]["data"]
            selector = data[:10]
            out = self.calls.get(selector)
            if isinstance(out, Exception):
                raise out
            return {"jsonrpc": "2.0", "id": 1, "result": "0x" + (out or b"").hex()}
        raise ValueError(f"unexpected RPC {method}")

    def is_connected(self, show_traceback: bool = False) -> bool:
        return True


def web3_factory_for(provider: FakeEVMProvider) -> Callable[[str, float], Web3]:
    seen: list[str] = []

    def factory(rpc_url: str, timeout: float) -> Web3:
        seen.append(rpc_url)
        return Web3(provider)

    factory.seen = seen  # type: ignore[attr-defined]
    return factory




def namespace() -> "types.SimpleNamespace":
    return types.SimpleNamespace(
        TOKEN=TOKEN,
        FEED=FEED,
        POOL=POOL,
        make_settings=make_settings,
        make_contract=make_contract,
        req=req,
        make_ctx=make_ctx,
        mock_http=mock_http,
        json_response=json_response,
        raw_evidence=raw_evidence,
        run=run,
        FakeEVMProvider=FakeEVMProvider,
        web3_factory_for=web3_factory_for,
        abi_encode=abi_encode,
    )
