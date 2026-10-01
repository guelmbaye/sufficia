"""End-to-end HTTP flow (Document 01 §6 'MVP success definition') on the local EvidenceGate."""

from __future__ import annotations

from decimal import Decimal

from api_helpers import GRAND_PRIZE


def test_health_and_system(client):
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/health/ready").json()
    assert ready["checks"]["database"] == "ok"
    info = client.get("/api/v1/system").json()
    assert info["engine_version"] == "1.0.0" and info["demo_mode"] is True
    assert info["chain"]["mode"] == "LOCAL_EVM" and info["chain"]["evidence_gate"].startswith("0x")
    assert "private" not in str(info).lower() and "0x" + "22" * 32 not in str(info)


def test_intent_compile_preview_and_freeze(client, flow):
    prev = client.post("/api/v1/intents/preview", json={"text": GRAND_PRIZE}).json()
    assert prev["preview"] is True
    reqs = {r["id"]: r for r in prev["contract"]["requirements"]}
    assert reqs["req_liquidity"]["threshold"] == "500000" and reqs["req_liquidity"]["operator"] == ">"
    assert reqs["req_inflow"]["threshold"] == "1000000"
    assert reqs["req_price"]["operator"] == "EXISTS" and reqs["req_price"]["origin"] == "IMPLICIT"

    intent = client.post("/api/v1/intents", json={"text": GRAND_PRIZE}).json()
    iid = intent["intent"]["intent_id"]
    assert intent["intent_hash"].startswith("0x") and len(intent["intent_hash"]) == 66
    c = client.post(f"/api/v1/intents/{iid}/compile").json()
    assert c["contract_hash"] == prev["contract_hash"]  # contract hash excludes ids -> same semantics, same hash
    again = client.post(f"/api/v1/intents/{iid}/compile")
    assert again.status_code == 409 and again.json()["error"]["code"] == "CONTRACT_FROZEN"
    assert client.get(f"/api/v1/intents/{iid}").json()["contract_id"] == c["contract"]["contract_id"]


def test_uncompilable_intent_is_422(client):
    iid = client.post("/api/v1/intents", json={"text": "make me rich"}).json()["intent"]["intent_id"]
    r = client.post(f"/api/v1/intents/{iid}/compile")
    assert r.status_code == 422 and r.json()["error"]["detail"]["missing"]


def test_grand_prize_flow_deny_then_authorize_execute_receipt_replay(client, flow):
    cid = flow.contract()
    ev = flow.post(f"/api/v1/contracts/{cid}/acquire", None, 201).json()
    liq = [v for v in ev["items"] if v["item"]["requirement_id"] == "req_liquidity"][0]
    assert Decimal(str(liq["item"]["claim"]["value"])) == 92440 and liq["badge"] == "SIMULATED · PAID"
    assert ev["payments"][0]["protocol"] == "x402" and ev["payments"][0]["status"] == "SIMULATED"

    deny = flow.post(f"/api/v1/contracts/{cid}/decide", None, 201).json()
    d = deny["decision"]
    assert d["state"] == "DENY" and d["reason_codes"] == ["THRESHOLD_NOT_MET"]
    assert "Available liquidity is $92,440" in d["summary"] and "100 USDC did not move" in d["summary"]
    assert deny["authority"] is None and deny["can_execute"] is False

    # Document 02 §23: the button is not the guard
    r = client.post(f"/api/v1/decisions/{d['decision_id']}/execute")
    assert r.status_code == 403 and r.json()["error"]["code"] == "EVIDENCE_AUTHORIZATION_REQUIRED"
    r = client.post(f"/api/v1/decisions/{d['decision_id']}/authorize")
    assert r.status_code == 403 and r.json()["error"]["detail"]["authorization"] == "NO_AUTHORIZATION"

    blocked = client.get(f"/api/v1/decisions/{d['decision_id']}/receipt").json()
    assert blocked["receipt"]["kind"] == "BLOCKED"
    assert blocked["receipt"]["execution"]["status"] == "BLOCKED" and blocked["receipt"]["execution"]["tx_hash"] is None
    assert blocked["receipt"]["reason_codes"] == ["THRESHOLD_NOT_MET"]

    # liquidity recovers -> fresh evidence -> AUTHORIZE
    assert client.put("/api/v1/demo/market", json={"asset": "TOKEN-X", "values": {"available_liquidity": "718000"}}).status_code == 200
    auth_dec = flow.decide(cid)
    d2 = auth_dec["decision"]
    assert d2["state"] == "AUTHORIZE" and d2["reason_codes"] == ["ALL_REQUIREMENTS_SATISFIED"]
    assert 0 < Decimal(auth_dec["authority"]["remaining_seconds"]) <= 30

    res = client.post(f"/api/v1/decisions/{d2['decision_id']}/execute")
    assert res.status_code == 200, res.text
    ex = res.json()
    assert ex["status"] == "EXECUTED" and ex["tx_hash"].startswith("0x")
    auth = ex["authorization"]
    assert auth["status"] == "CONSUMED" and auth["evidence_root"] == d2["evidence_root"]
    assert auth["authorize_tx_hash"] and auth["register_tx_hash"]

    rc = client.get(f"/api/v1/receipts/{ex['receipt_id']}").json()
    body = rc["receipt"]
    assert body["kind"] == "EXECUTED" and body["decision_state"] == "AUTHORIZE"
    assert body["execution"]["tx_hash"] == ex["tx_hash"] and Decimal(str(body["execution"]["amount_usdc"])) == 100
    assert body["action_hash"] == auth["action_hash"] and body["evidence_root"] == d2["evidence_root"]
    assert rc["commitment"]["status"] == "CONFIRMED"
    assert [s["step"] for s in rc["audit_chain"]] == [
        "Evidence Contract Hash", "Evidence Root", "Decision", "Action Hash", "Authorization ID",
        "Authorization TX", "Execution TX", "Receipt Hash",
    ]

    rp = client.post(f"/api/v1/receipts/{ex['receipt_id']}/replay").json()
    assert rp["result"] == "MATCH" and rp["market_queried"] is False and rp["chain_verification"] == "VERIFIED"
    assert all(c["ok"] for c in rp["checks"])
    assert client.post(f"/api/v1/receipts/{blocked['receipt']['receipt_id']}/replay").json()["result"] == "MATCH"

    # idempotent re-execution: same execution, no second transaction
    again = client.post(f"/api/v1/decisions/{d2['decision_id']}/execute").json()
    assert again["idempotent"] is True and again["execution_id"] == ex["execution_id"]

    assert client.get("/api/v1/audit/verify").json()["valid"] is True
    purposes = [t["purpose"] for t in client.get("/api/v1/chain/transactions").json()["transactions"]]
    for p in ("INTENT_REGISTER", "AUTHORIZATION", "PROTECTED_EXECUTION", "RECEIPT_COMMITMENT"):
        assert p in purposes


def test_verify_is_a_dry_run(client, flow):
    cid = flow.contract()
    flow.post(f"/api/v1/contracts/{cid}/acquire", None, 201)
    v = flow.post(f"/api/v1/contracts/{cid}/verify", None, 200).json()
    assert v["preview"] is True and v["state"] == "DENY"
    assert client.get(f"/api/v1/contracts/{cid}").json()["decisions"] == []


def test_decide_requires_evidence(client, flow):
    cid = flow.contract()
    r = client.post(f"/api/v1/contracts/{cid}/decide")
    assert r.status_code == 409 and r.json()["error"]["code"] == "EVIDENCE_NOT_ACQUIRED"


def test_numbers_are_exact_strings_never_floats(client, flow):
    cid = flow.contract()
    ev = flow.post(f"/api/v1/contracts/{cid}/acquire", None, 201).json()
    price = [v for v in ev["items"] if v["item"]["requirement_id"] == "req_price"][0]
    assert price["item"]["claim"]["value"] == "0.428"  # Decimal -> exact string, never a float
    assert isinstance(ev["payments"][0]["quoted"], str) and Decimal(ev["payments"][0]["quoted"]) == Decimal("0.02")


def test_unknown_ids_are_404(client):
    for path in ("/api/v1/intents/nope", "/api/v1/contracts/nope", "/api/v1/decisions/nope", "/api/v1/receipts/nope",
                 "/api/v1/authorizations/0x" + "00" * 32):
        r = client.get(path)
        assert r.status_code == 404 and r.json()["error"]["code"].endswith("NOT_FOUND")
    assert client.post("/api/v1/decisions/nope/execute").status_code == 404


def test_raw_token_amounts_beyond_2_53_are_strings(client, flow):
    client.put("/api/v1/demo/market", json={"values": {"available_liquidity": "718000"}})
    d = flow.decide(flow.contract())["decision"]
    ex = client.post(f"/api/v1/decisions/{d['decision_id']}/execute").json()
    action = ex["authorization"]["action"]
    assert isinstance(action["min_amount_out"], str) and int(action["min_amount_out"]) > 2**53
    assert action["max_amount_in"] == 100_000_000  # 100 USDC (6 decimals) fits a JS number


def test_alembic_migration_matches_the_orm(tmp_path):
    """The committed migration creates exactly the ORM schema (upgrade) and removes it (downgrade)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    import sqlalchemy as sa

    from sufficia.db.models import Base

    db = tmp_path / "m.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db}")
    root = Path(__file__).resolve().parents[2]
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root, env=env, check=True, capture_output=True)
    insp = sa.inspect(sa.create_engine(f"sqlite:///{db}"))
    assert set(insp.get_table_names()) - {"alembic_version"} == set(Base.metadata.tables)
    for name, table in Base.metadata.tables.items():
        assert {c["name"] for c in insp.get_columns(name)} == {c.name for c in table.columns}, name
    subprocess.run([sys.executable, "-m", "alembic", "downgrade", "base"], cwd=root, env=env, check=True, capture_output=True)


def test_execution_exposes_decoded_gate_events(client, flow):
    client.put("/api/v1/demo/market", json={"values": {"available_liquidity": "718000"}})
    d = flow.decide(flow.contract())["decision"]
    ex = client.post(f"/api/v1/decisions/{d['decision_id']}/execute").json()
    events = {t["purpose"]: [e["event"] for e in t["events"]] for t in ex["transactions"]}
    assert events == {"PROTECTED_EXECUTION": ["AuthorizationConsumed", "ProtectedExecution"], "RECEIPT_COMMITMENT": ["ReceiptCommitted"]}
    detail = client.get(f"/api/v1/authorizations/{ex['authorization']['authorization_id']}").json()
    by_purpose = {t["purpose"]: [e["event"] for e in t["events"]] for t in detail["transactions"]}
    assert by_purpose["INTENT_REGISTER"] == ["IntentRegistered"] and by_purpose["AUTHORIZATION"] == ["ExecutionAuthorized"]
    assert by_purpose["RECEIPT_COMMITMENT"] == ["ReceiptCommitted"]
