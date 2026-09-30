"""Black-box integration test: a real uvicorn process, real HTTP, the real EvidenceGate bytecode.

    python -m pytest tests/integration -q        (from the repository root)

Covers Document 01 §6 end to end: submit intent -> contract -> evidence -> one mandatory condition
fails -> transaction blocked (403) -> valid evidence -> authorization -> protected execution ->
receipt -> replay, plus the audit chain.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "apps" / "api"
GRAND_PRIZE = "Buy up to 100 USDC of TOKEN-X only if smart-money inflows exceed $1M and liquidity exceeds $500K."


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    port = _free_port()
    db = tmp_path_factory.mktemp("db") / "e2e.db"
    env = dict(
        os.environ,
        PYTHONPATH=str(API),
        DATABASE_URL=f"sqlite:///{db}",
        CHAIN_MODE="LOCAL_EVM",
        EVIDENCE_PROFILE="demo",
        DEMO_MODE="true",
        INTENT_COMPILER="deterministic",
        ANTHROPIC_API_KEY="",
        LOG_LEVEL="WARNING",
        API_KEY="",
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "sufficia.api.app:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=API, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            if httpx.get(url + "/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.25)
    else:  # pragma: no cover
        proc.kill()
        raise RuntimeError(proc.stdout.read().decode() if proc.stdout else "server did not start")
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover
        proc.kill()


def test_grand_prize_over_real_http(base_url):
    c = httpx.Client(base_url=base_url + "/api/v1", timeout=60)
    iid = c.post("/intents", json={"text": GRAND_PRIZE}).json()["intent"]["intent_id"]
    contract = c.post(f"/intents/{iid}/compile").json()
    cid = contract["contract"]["contract_id"]
    assert {r["id"] for r in contract["contract"]["requirements"]} == {"req_inflow", "req_liquidity", "req_price"}

    assert c.post(f"/contracts/{cid}/acquire").status_code == 201
    deny = c.post(f"/contracts/{cid}/decide").json()["decision"]
    assert deny["state"] == "DENY" and deny["reason_codes"] == ["THRESHOLD_NOT_MET"]
    blocked = c.post(f"/decisions/{deny['decision_id']}/execute")
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "EVIDENCE_AUTHORIZATION_REQUIRED"

    assert c.put("/demo/market", json={"values": {"available_liquidity": "718000"}}).status_code == 200
    assert c.post(f"/contracts/{cid}/acquire").status_code == 201
    auth = c.post(f"/contracts/{cid}/decide").json()["decision"]
    assert auth["state"] == "AUTHORIZE"

    ex = c.post(f"/decisions/{auth['decision_id']}/execute").json()
    assert ex["status"] == "EXECUTED" and ex["tx_hash"].startswith("0x") and len(ex["tx_hash"]) == 66
    receipt = c.get(f"/receipts/{ex['receipt_id']}").json()
    assert receipt["receipt"]["execution"]["tx_hash"] == ex["tx_hash"]
    assert receipt["commitment"]["status"] == "CONFIRMED"

    replay = c.post(f"/receipts/{ex['receipt_id']}/replay").json()
    assert replay["result"] == "MATCH" and replay["chain_verification"] == "VERIFIED"
    assert c.get("/audit/verify").json()["valid"] is True


def test_every_state_is_reproducible_over_http(base_url):
    c = httpx.Client(base_url=base_url, timeout=120)
    expected = {"valid-v1": "AUTHORIZE", "threshold-fail-v1": "DENY", "missing-v1": "ABSTAIN",
                "stale-v1": "REFRESH", "conflict-v1": "ESCALATE"}
    for sid, state in expected.items():
        run = c.post(f"/demo/scenarios/{sid}/run").json()
        assert run["passed"] is True and run["final_state"] == state, sid


def test_openapi_documents_the_guarded_endpoints(base_url):
    spec = httpx.get(base_url + "/openapi.json").json()
    for path in ("/api/v1/decisions/{decision_id}/execute", "/api/v1/authorizations/{authorization_id}/execute",
                 "/api/v1/receipts/{receipt_id}/replay", "/api/v1/contracts/{contract_id}/decide"):
        assert path in spec["paths"]
