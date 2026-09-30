"""Shared builders for the Decision Engine test suite (imported by the test modules).

``conftest.py`` puts this directory and ``apps/api`` on ``sys.path`` so ``import engine_builders``
works under every pytest import mode without ``__init__.py`` files.
"""

from __future__ import annotations

import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[2]
if str(API_ROOT) not in sys.path:  # allow running without installing the package
    sys.path.insert(0, str(API_ROOT))

from sufficia.canonical.hashes import authorization_nonce  # noqa: E402
from sufficia.decision_engine import evaluate, unix_seconds_floor  # noqa: E402
from sufficia.domain.models import (  # noqa: E402
    AcquisitionFailure,
    CanonicalAction,
    Decision,
    EvidenceContract,
    EvidenceItem,
    EvidenceSet,
)

T0 = datetime(2026, 10, 5, 18, 42, 15, tzinfo=timezone.utc)
CID = "ec_test"
H_REQ = "0x" + "a1" * 32
H_RESP = "0x" + "b2" * 32
EXECUTOR = "0x6666666666666666666666666666666666666666"
INTENT32 = "0x" + "77" * 32

REQUIREMENTS = {
    "req_inflow": ("smart_money_inflow_24h", ">", 1_000_000),
    "req_liquidity": ("available_liquidity", ">", 500_000),
    "req_price": ("asset_price", "EXISTS", None),
}
GOOD = {"req_inflow": Decimal(1_340_000), "req_liquidity": Decimal(718_000), "req_price": Decimal("0.428")}
PROVIDERS = {"req_inflow": "smartmoney-api", "req_liquidity": "dex-liquidity-api", "req_price": "price-api"}


def contract_dict(**overrides: Any) -> dict:
    reqs = []
    for rid, (claim, op, threshold) in REQUIREMENTS.items():
        r: dict[str, Any] = {"id": rid, "claim": claim, "operator": op, "unit": "USD", "max_age_seconds": 30}
        if threshold is not None:
            r["threshold"] = Decimal(threshold)
        reqs.append(r)
    d: dict[str, Any] = {
        "contract_id": CID,
        "intent_id": "intent_test",
        "action": {"type": "BUY", "asset": "TOKEN-X", "max_amount": {"value": Decimal(100), "currency": "USDC"}},
        "requirements": reqs,
        "policy": {"minimum_required_evidence": 3},
        "economic_limits": {"max_execution_value_usd": Decimal(100), "max_evidence_cost_usd": Decimal("0.25")},
        "authorization": {"ttl_seconds": 30},
    }
    d.update(overrides)
    return d


def make_contract(**overrides: Any) -> EvidenceContract:
    return EvidenceContract.model_validate(contract_dict(**overrides))


def with_requirement(contract: EvidenceContract, rid: str, **changes: Any) -> EvidenceContract:
    d = contract.model_dump(mode="python")
    for r in d["requirements"]:
        if r["id"] == rid:
            r.update(changes)
    return EvidenceContract.model_validate(d)


def make_item(
    requirement_id: str = "req_liquidity",
    value: Any = None,
    *,
    evidence_id: str | None = None,
    age: Any = 5,
    observed_at: Any = "auto",
    t0: datetime = T0,
    provider: str | None = None,
    unit: str | None = "USD",
    claim: str | None = None,
    contract_id: str = CID,
    source_type: str = "API",
    source_family: str | None = "auto",
    upstream_source: str | None = None,
    request_hash: str | None = H_REQ,
    response_hash: str | None = H_RESP,
    cost: Any = 0,
    endpoint_id: str | None = None,
    chain_ref: dict | None = None,
    derivation: dict | None = None,
    mode: str = "SIMULATED",
) -> EvidenceItem:
    if value is None:
        value = GOOD[requirement_id]
    if observed_at == "auto":
        observed_at = t0 - timedelta(microseconds=int(Decimal(str(age)) * 1_000_000))
    prov = PROVIDERS.get(requirement_id, "provider") if provider is None else provider
    body: dict[str, Any] = {
        "evidence_id": evidence_id or f"ev_{requirement_id}",
        "contract_id": contract_id,
        "requirement_id": requirement_id,
        "claim": {"name": claim or REQUIREMENTS.get(requirement_id, ("available_liquidity",))[0], "value": value, "unit": unit},
        "temporal": {"observed_at": observed_at},
        "source": {
            "provider": prov,
            "source_type": source_type,
            "mode": mode,
            "endpoint_id": endpoint_id,
            "source_family": (prov + "-family") if source_family == "auto" else source_family,
            "upstream_source": upstream_source,
        },
        "integrity": {"request_hash": request_hash, "response_hash": response_hash, "chain_ref": chain_ref},
        "economic": {"cost": Decimal(str(cost))},
    }
    if derivation is not None:
        body["derivation"] = derivation
    return EvidenceItem.model_validate(body)


def good_items(**per_req: dict) -> list[EvidenceItem]:
    return [make_item(rid, **per_req.get(rid, {})) for rid in REQUIREMENTS]


def make_set(items: list[EvidenceItem], failures: list[dict] | None = None, contract_id: str = CID) -> EvidenceSet:
    return EvidenceSet(
        contract_id=contract_id,
        items=items,
        acquisition_failures=[AcquisitionFailure.model_validate(f) for f in (failures or [])],
    )


def decide(items: list[EvidenceItem] | None = None, contract: EvidenceContract | None = None, *, failures=None, t: datetime = T0) -> Decision:
    contract = contract or make_contract()
    return evaluate(contract, make_set(good_items() if items is None else items, failures, contract.contract_id), t)


def action_dict(**overrides: Any) -> dict:
    d = {
        "action_type": "BUY",
        "asset": "TOKEN-X",
        "amount": {"value": Decimal(100), "currency": "USDC"},
        "chain_id": 10143,
        "gate_address": "0x1111111111111111111111111111111111111111",
        "target": "0x2222222222222222222222222222222222222222",
        "value": 0,
        "data": "0x",
        "asset_in": "0x3333333333333333333333333333333333333333",
        "max_amount_in": 100_000_000,
        "asset_out": "0x4444444444444444444444444444444444444444",
        "min_amount_out": 233_600_000_000_000_000_000,
        "recipient": "0x5555555555555555555555555555555555555555",
        "deadline": unix_seconds_floor(T0) + 20,
        "nonce": "0x" + "00" * 32,
        "adapter": "demo-swap-router-v1",
    }
    d.update(overrides)
    return d


def action_for(decision: Decision, **overrides: Any) -> CanonicalAction:
    base = action_dict(nonce=authorization_nonce(decision.fingerprint))
    if decision.authority_window is not None:
        base["deadline"] = unix_seconds_floor(decision.authority_window.expires_at)
    base.update(overrides)
    return CanonicalAction.model_validate(base)


def codes(decision: Decision) -> list[str]:
    return [c.value for c in decision.reason_codes]


def assessment(decision: Decision, evidence_id: str):
    found = [a for a in decision.evidence_assessments if a.evidence_id == evidence_id]
    assert found, f"no assessment for {evidence_id}"
    return found[0]


def clone(obj: Any) -> Any:
    return deepcopy(obj)
