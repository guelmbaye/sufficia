"""An intent's protected amount moves at most once, and actions this server cannot execute are said up front."""

from __future__ import annotations


def _valid(flow, cid: str) -> dict:
    return flow.post(f"/api/v1/demo/contracts/{cid}/test/valid", None, 201).json()["decision"]


def test_an_executed_intent_cannot_be_authorized_again(client, flow):
    cid = flow.contract()
    first = _valid(flow, cid)
    ex = client.post(f"/api/v1/decisions/{first['decision']['decision_id']}/execute").json()
    assert ex["status"] == "EXECUTED"

    second = _valid(flow, cid)  # fresh, valid evidence: still a faithful AUTHORIZE decision...
    assert second["decision"]["state"] == "AUTHORIZE"
    assert second["can_execute"] is False and second["spent_by"]["receipt_id"] == ex["receipt_id"]
    before = len(client.get("/api/v1/chain/transactions").json()["transactions"])

    r = client.post(f"/api/v1/decisions/{second['decision']['decision_id']}/authorize")  # ...but no new authority
    assert r.status_code == 409 and r.json()["error"]["code"] == "INTENT_ALREADY_EXECUTED"
    assert r.json()["error"]["detail"]["tx_hash"] == ex["tx_hash"]
    r = client.post(f"/api/v1/decisions/{second['decision']['decision_id']}/execute")
    assert r.status_code == 409 and r.json()["error"]["code"] == "INTENT_ALREADY_EXECUTED"
    assert len(client.get("/api/v1/chain/transactions").json()["transactions"]) == before  # nothing submitted

    view = client.get(f"/api/v1/contracts/{cid}").json()
    assert view["spent_by"]["execution_id"] == ex["execution_id"]
    # the decision that did execute is not "spent by" itself
    assert client.get(f"/api/v1/decisions/{first['decision']['decision_id']}").json()["spent_by"] is None


def test_two_authorizations_issued_before_the_first_execution_cannot_both_execute(client, flow):
    cid = flow.contract()
    a = _valid(flow, cid)["decision"]["decision_id"]
    b = _valid(flow, cid)["decision"]["decision_id"]
    auth_a = flow.post(f"/api/v1/decisions/{a}/authorize").json()["authorization_id"]
    auth_b = flow.post(f"/api/v1/decisions/{b}/authorize").json()["authorization_id"]
    assert flow.post(f"/api/v1/authorizations/{auth_a}/execute").json()["status"] == "EXECUTED"
    r = client.post(f"/api/v1/authorizations/{auth_b}/execute")
    assert r.status_code == 409 and r.json()["error"]["code"] == "INTENT_ALREADY_EXECUTED"
    assert client.get(f"/api/v1/authorizations/{auth_b}").json()["onchain"]["consumed"] is False


def test_unsupported_actions_are_reported_before_any_evidence(client, flow):
    text = "Sell up to 50 USDC of TOKEN-X only if liquidity exceeds $500K"
    preview = flow.post("/api/v1/intents/preview", {"text": text}).json()
    assert preview["execution"] == {
        "executable": False,
        "adapter": "demo_swap",
        "label": "DemoSwapRouter",
        "code": "UNSUPPORTED_ACTION",
        "message": "demo_swap only executes BUY actions, got SELL",
    }
    cid = flow.contract(text)
    assert client.get(f"/api/v1/contracts/{cid}").json()["execution"]["code"] == "UNSUPPORTED_ACTION"
    d = _valid(flow, cid)
    assert d["decision"]["state"] == "AUTHORIZE" and d["can_execute"] is False
    assert client.post(f"/api/v1/decisions/{d['decision']['decision_id']}/authorize").json()["error"]["code"] == "UNSUPPORTED_ACTION"


def test_buy_token_x_is_executable(client, flow):
    preview = flow.post("/api/v1/intents/preview", {"text": "Buy up to 10 USDC of TOKEN-X only if liquidity exceeds $500K"}).json()
    assert preview["execution"]["executable"] is True and preview["execution"]["code"] is None
