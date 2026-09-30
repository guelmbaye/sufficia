"""Backend support for the web app: 'Test the Gate' conditions, receipt timeline, tamper test."""

from __future__ import annotations

import pytest

EXPECTED = {"valid": "AUTHORIZE", "threshold": "DENY", "missing": "ABSTAIN", "stale": "REFRESH", "conflict": "ESCALATE"}


@pytest.mark.parametrize("condition,state", sorted(EXPECTED.items()))
def test_each_test_condition_changes_only_the_evidence(client, flow, condition, state):
    cid = flow.contract()
    r = flow.post(f"/api/v1/demo/contracts/{cid}/test/{condition}", None, 201).json()
    assert r["expected_state"] == state and r["decision"]["decision"]["state"] == state
    assert r["decision"]["decision"]["contract_id"] == cid  # same contract: only the evidence changed


def test_conditions_are_order_independent_on_one_contract(client, flow):
    """Document 05 §26: the judge can click the conditions in any order and always get the same states."""
    cid = flow.contract()
    for condition in ["threshold", "stale", "conflict", "valid", "missing", "valid", "threshold"]:
        r = flow.post(f"/api/v1/demo/contracts/{cid}/test/{condition}", None, 201).json()
        assert r["decision"]["decision"]["state"] == EXPECTED[condition], condition
    decisions = client.get(f"/api/v1/contracts/{cid}").json()["decisions"]
    assert [d["state"] for d in decisions][-2:] == ["AUTHORIZE", "DENY"]


def test_threshold_condition_shows_the_canonical_failing_value(client, flow):
    cid = flow.contract()
    r = flow.post(f"/api/v1/demo/contracts/{cid}/test/threshold", None, 201).json()
    liq = [v for v in r["evidence"]["items"] if v["item"]["requirement_id"] == "req_liquidity"][0]
    assert str(liq["item"]["claim"]["value"]) == "92440" and liq["badge"] == "SIMULATED · PAID"
    assert "Available liquidity is $92,440" in r["decision"]["decision"]["summary"]


def test_unknown_condition_is_404(client, flow):
    cid = flow.contract()
    assert client.post(f"/api/v1/demo/contracts/{cid}/test/explode").status_code == 404


def test_receipt_timeline_executed(client, flow):
    cid = flow.contract()
    d = flow.post(f"/api/v1/demo/contracts/{cid}/test/valid", None, 201).json()["decision"]["decision"]
    ex = client.post(f"/api/v1/decisions/{d['decision_id']}/execute").json()
    rc = client.get(f"/api/v1/receipts/{ex['receipt_id']}").json()
    steps = [s["step"] for s in rc["timeline"]]
    assert steps == ["intent", "contract", "evidence", "decision", "authorized", "committed", "executed", "receipt"]
    times = [s["at"] for s in rc["timeline"]]
    assert all(times) and times == sorted(times)  # chronological, from stored timestamps
    assert rc["timeline"][6]["tx_hash"] == ex["tx_hash"]


def test_receipt_timeline_blocked(client, flow):
    cid = flow.contract()
    d = flow.post(f"/api/v1/demo/contracts/{cid}/test/threshold", None, 201).json()["decision"]["decision"]
    rc = client.get(f"/api/v1/decisions/{d['decision_id']}/receipt").json()
    assert [s["step"] for s in rc["timeline"]] == ["intent", "contract", "evidence", "decision", "blocked", "receipt"]


def test_tamper_test_breaks_integrity_without_modifying_anything(client, flow):
    cid = flow.contract()
    d = flow.post(f"/api/v1/demo/contracts/{cid}/test/valid", None, 201).json()["decision"]["decision"]
    ex = client.post(f"/api/v1/decisions/{d['decision_id']}/execute").json()
    t = client.post(f"/api/v1/receipts/{ex['receipt_id']}/tamper-test").json()
    assert t["claim"] == "available_liquidity" and str(t["original_value"]) == "718000" and str(t["tampered_value"]) == "718001"
    assert t["recomputed_evidence_root"] == t["original_evidence_root"]
    assert t["evidence_root"] == "MISMATCH" and t["receipt_integrity"] == "INVALID"
    assert t["stored_data_modified"] is False
    assert client.post(f"/api/v1/receipts/{ex['receipt_id']}/replay").json()["result"] == "MATCH"


def test_replay_reports_the_same_explanation(client, flow):
    cid = flow.contract()
    d = flow.post(f"/api/v1/demo/contracts/{cid}/test/threshold", None, 201).json()["decision"]["decision"]
    rid = client.get(f"/api/v1/decisions/{d['decision_id']}/receipt").json()["receipt"]["receipt_id"]
    rp = client.post(f"/api/v1/receipts/{rid}/replay").json()
    assert rp["result"] == "MATCH"
    assert rp["original"]["reason"] == rp["replay"]["reason"] and "Available liquidity is $92,440" in rp["replay"]["reason"]
    assert {c["check"] for c in rp["checks"]} >= {"engine_version", "explanation"}
