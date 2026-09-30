"""The Python SDK against the real app (in-process transport)."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "packages" / "sdk" / "python"))
from sufficia_sdk import EvidenceGate, SufficiaError  # noqa: E402

from api_helpers import GRAND_PRIZE  # noqa: E402


def test_sdk_evaluate_execute_replay(client):
    gate = EvidenceGate(client=httpx.Client(transport=client._transport, base_url="http://testserver/api/v1"))
    d = gate.evaluate(GRAND_PRIZE)
    assert d.state == "DENY" and not d.authorized
    with pytest.raises(SufficiaError) as exc:
        gate.execute(d)
    assert exc.value.status == 403 and exc.value.code == "EVIDENCE_AUTHORIZATION_REQUIRED"
    client.put("/api/v1/demo/market", json={"values": {"available_liquidity": "718000"}})
    d2 = gate.evaluate(GRAND_PRIZE, contract_id=d.contract_id)
    assert d2.authorized
    ex = gate.execute(d2)
    assert ex["status"] == "EXECUTED"
    assert gate.replay(ex["receipt_id"])["result"] == "MATCH"
