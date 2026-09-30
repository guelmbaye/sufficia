"""Live integration suite (Document 06 §115): real Monad RPC, real x402 facilitator, real data providers.

Deselected by default (`-m "not live"` in pyproject). Run before a demo / submission:

    SUFFICIA_LIVE=1 MONAD_RPC_URL=... python -m pytest -m live tests/live -v

Each test skips itself when its prerequisites (network, keys, deployment) are absent, so a provider
outage can never turn the hermetic benchmark red.
"""

from __future__ import annotations

import os

import httpx
import pytest

from sufficia.config import Settings

pytestmark = pytest.mark.live

LIVE = os.environ.get("SUFFICIA_LIVE") == "1"


def _settings() -> Settings:
    return Settings()


@pytest.mark.skipif(not LIVE, reason="set SUFFICIA_LIVE=1")
def test_monad_rpc_chain_id():
    s = _settings()
    r = httpx.post(s.monad_rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}, timeout=10)
    assert int(r.json()["result"], 16) in (10143, 143)


@pytest.mark.skipif(not LIVE, reason="set SUFFICIA_LIVE=1")
def test_x402_facilitator_supported_kinds():
    s = _settings()
    r = httpx.get(s.x402_facilitator.rstrip("/") + "/supported", timeout=10)
    assert r.status_code == 200
    assert "eip155:10143" in r.text or "monad" in r.text.lower()


@pytest.mark.skipif(not (LIVE and os.environ.get("NANSEN_API_KEY") and os.environ.get("ASSET_TOKEN_ADDRESS")),
                    reason="needs SUFFICIA_LIVE=1, NANSEN_API_KEY and ASSET_TOKEN_ADDRESS")
def test_nansen_smart_money_netflow_is_normalized():
    import asyncio

    from sufficia.domain.models import EvidenceContract
    from sufficia.evidence.adapters.nansen import NansenSmartMoneyAdapter
    from sufficia.evidence.base import AcquisitionContext, EvidenceBudget
    from sufficia.intent import compile_intent
    from sufficia.domain.models import Intent
    from datetime import datetime, timezone

    s = _settings()
    c = compile_intent(Intent(intent_id="live", text="Buy up to 10 USDC of TOKEN-X only if smart-money inflows exceed $1M",
                              created_at=datetime.now(timezone.utc)), contract_id="live", settings=s).contract
    req = c.requirement("req_inflow")

    async def run():
        async with httpx.AsyncClient(timeout=15) as http:
            ctx = AcquisitionContext(contract=c, run_id="live", settings=s, budget=EvidenceBudget(c.economic_limits.max_evidence_cost_usd), http=http)
            return await NansenSmartMoneyAdapter(s).fetch(req, ctx)

    raw = asyncio.run(run())
    assert raw.unit == "USD" and raw.provider == "nansen"


@pytest.mark.skipif(not (LIVE and os.environ.get("CHAIN_MODE", "").upper() == "MONAD_TESTNET"),
                    reason="needs SUFFICIA_LIVE=1 and a CHAIN_MODE=MONAD_TESTNET deployment")
def test_grand_prize_on_monad_testnet():
    """Full DENY -> AUTHORIZE -> protected execution -> receipt -> replay against the deployed EvidenceGate."""
    from fastapi.testclient import TestClient

    from sufficia.api.app import create_app

    with TestClient(create_app(Settings(database_url="sqlite://"))) as c:
        run = c.post("/api/v1/demo/scenarios/grand-prize-v1/run", json={}).json()
        assert run["passed"] is True, run
        ex = [s for s in run["timeline"] if s["op"] == "execute"][0]
        assert ex["status"] == "EXECUTED" and ex["tx_hash"]
