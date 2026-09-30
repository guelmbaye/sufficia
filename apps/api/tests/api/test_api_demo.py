"""Demo Lab & scenario orchestration over HTTP (Document 02 §32-33, Document 05 §57 & §114)."""

from __future__ import annotations

import pytest

EXPECTED = {
    "grand-prize-v1": "AUTHORIZE",
    "threshold-fail-v1": "DENY",
    "missing-v1": "ABSTAIN",
    "stale-v1": "REFRESH",
    "conflict-v1": "ESCALATE",
    "valid-v1": "AUTHORIZE",
}


def test_scenarios_are_listed_and_versioned(client):
    listed = {s["scenario_id"]: s for s in client.get("/api/v1/demo/scenarios").json()["scenarios"]}
    assert set(EXPECTED) <= set(listed)
    assert all(s["version"] == 1 for s in listed.values())
    assert client.get("/api/v1/demo/scenarios/../../etc/passwd").status_code == 404
    assert client.get("/api/v1/demo/scenarios/nope").status_code == 404


@pytest.mark.parametrize("scenario_id,final", sorted(EXPECTED.items()))
def test_every_scenario_reproduces_its_state(client, scenario_id, final):
    r = client.post(f"/api/v1/demo/scenarios/{scenario_id}/run", json={})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["passed"] is True and run["final_state"] == final
    assert all(step["ok"] for step in run["timeline"])
    replays = [s for s in run["timeline"] if s["op"] == "replay"]
    assert replays and all(s["result"] == "MATCH" for s in replays)


def test_grand_prize_timeline_tells_the_causal_story(client):
    run = client.post("/demo/scenarios/grand-prize-v1/run").json()  # Document 05 §114 path
    ops = [s["op"] for s in run["timeline"]]
    assert ops == ["intent", "compile", "acquire", "decide", "attempt_execute", "market", "acquire", "decide", "execute", "replay"]
    deny, auth = [s for s in run["timeline"] if s["op"] == "decide"]
    assert deny["state"] == "DENY" and deny["reason_codes"] == ["THRESHOLD_NOT_MET"]
    assert run["timeline"][4]["error"]["status"] == 403
    assert auth["state"] == "AUTHORIZE"
    ex = run["timeline"][8]
    assert ex["status"] == "EXECUTED" and ex["tx_hash"] and ex["authorization"]["authorize_tx_hash"]
    assert {t["purpose"] for t in ex["transactions"]} >= {"PROTECTED_EXECUTION"}


def test_scenario_can_stop_before_execution(client):
    run = client.post("/api/v1/demo/scenarios/valid-v1/run", json={"execute": False}).json()
    ex = [s for s in run["timeline"] if s["op"] == "execute"][0]
    assert ex["skipped"] is True and run["final_state"] == "AUTHORIZE"


def test_inject_by_evidence_id_changes_exactly_one_item(client, flow):
    client.put("/api/v1/demo/market", json={"values": {"available_liquidity": "718000"}})
    cid = flow.contract()
    ev = flow.post(f"/api/v1/contracts/{cid}/acquire", None, 201).json()
    liq = [v["item"] for v in ev["items"] if v["item"]["requirement_id"] == "req_liquidity"][0]
    inj = flow.post(f"/api/v1/demo/evidence/{liq['evidence_id']}/threshold", None, 201).json()
    assert inj["parent_id"] == ev["evidence_set_id"] and inj["evidence_root"] != ev["evidence_root"]
    before = {v["item"]["evidence_id"] for v in ev["items"]}
    after = {v["item"]["evidence_id"] for v in inj["items"]}
    assert len(before - after) == 1 and len(after - before) == 1
    new = [v for v in inj["items"] if v["item"]["evidence_id"] in after - before][0]
    assert new["badge"] == "INJECTED" and new["item"]["source"]["provider"] == "demo-lab"
    assert str(new["item"]["claim"]["value"]) == "92440"
    d = flow.post(f"/api/v1/contracts/{cid}/decide", None, 201).json()["decision"]
    assert d["state"] == "DENY"
    # restore with a 'valid' injection on the injected item -> AUTHORIZE
    flow.post(f"/api/v1/demo/evidence/{new['item']['evidence_id']}/valid", None, 201)
    assert flow.post(f"/api/v1/contracts/{cid}/decide", None, 201).json()["decision"]["state"] == "AUTHORIZE"
    # an item that is no longer current cannot be targeted
    r = client.post(f"/api/v1/demo/evidence/{liq['evidence_id']}/stale")
    assert r.status_code == 409 and r.json()["error"]["code"] == "EVIDENCE_NOT_CURRENT"


def test_market_validation(client):
    r = client.put("/api/v1/demo/market", json={"values": {"available_liquidity": "lots"}})
    assert r.status_code == 422
    r = client.put("/api/v1/demo/market", json={"values": {"sanctions_flag": "1"}})
    assert r.status_code == 422
    m = client.put("/api/v1/demo/market", json={"values": {"available_liquidity": "123456.78"}}).json()
    assert m["overrides"]["TOKEN-X"]["available_liquidity"] == "123456.78"
    assert client.post("/api/v1/demo/market/reset").json()["overrides"] == {}
