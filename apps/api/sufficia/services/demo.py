"""Demo Lab (Document 02 §32-33, Document 05 §92) and the scenario orchestrator (Document 05 §114-115).

The Demo Lab changes exactly one Evidence Item per action and produces a NEW immutable evidence set.
Every injected observation is labelled ``INJECTED`` and attributed to the provider ``demo-lab``
(never to a real provider), with provenance hashes over the injection request and the injected value.
Injection never bypasses the engine: the next decision evaluates the injected set like any other.

Scenarios are versioned JSON files (demo/scenarios/<id>.json). ``run`` coordinates reset -> compile
-> acquire -> inject -> decide -> execute -> replay and returns the full timeline, so the UI does not
depend on clicking independent backend operations during the main demo.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from eth_utils import keccak

from ..canonical.canonicalize import canonical_bytes
from ..canonical.hashes import REQUEST_DOMAIN, RESPONSE_DOMAIN, raw_hash
from ..decision_engine.rules import satisfies
from ..domain.claims import get_claim
from ..domain.enums import Operator, SourceMode, SourceType
from ..domain.models import (
    EvidenceClaim,
    EvidenceContract,
    EvidenceEconomic,
    EvidenceIntegrity,
    EvidenceItem,
    EvidenceSource,
    EvidenceTemporal,
    Requirement,
)
from .errors import ApiError, not_found
from .workflow import Sufficia

DEMO_PROVIDER = "demo-lab"
CONDITIONS = ("valid", "stale", "remove", "conflict", "threshold")

PASS_PRESETS = {
    "smart_money_inflow_24h": Decimal("1340000"),
    "available_liquidity": Decimal("718000"),
    "asset_price": Decimal("0.428"),
}
FAIL_PRESETS = {
    "smart_money_inflow_24h": Decimal("640000"),
    "available_liquidity": Decimal("92440"),
}
STALE_EXTRA_SECONDS = 90  # 30 s max age -> 120 s old (Document 02 §33 F3)


def _passing_value(req: Requirement) -> Any:
    spec = get_claim(req.claim)
    if spec is not None and spec.unit == "BOOL":
        return True if req.operator != Operator.EQ else bool(req.expected)
    if req.operator == Operator.IN:
        return sorted(req.values or [""])[0]
    preset = PASS_PRESETS.get(req.claim, Decimal("1"))
    if req.operator in (Operator.EXISTS, Operator.NOT_EXISTS):
        return preset
    if satisfies(preset, req):
        return preset
    t = req.threshold if req.threshold is not None else Decimal(0)
    if req.operator in (Operator.GT, Operator.GTE):
        return t + abs(t) + 1
    if req.operator in (Operator.LT, Operator.LTE):
        return t / 2 if t > 0 else t - abs(t) - 1
    if req.operator == Operator.EQ:
        return t
    return t + 1  # NEQ


def _failing_value(req: Requirement) -> Any:
    if req.operator in (Operator.EXISTS, Operator.NOT_EXISTS, Operator.IN) or req.threshold is None:
        raise ApiError(422, "CONDITION_NOT_APPLICABLE", f"{req.id} ({req.operator.value}) has no numeric threshold to fail")
    preset = FAIL_PRESETS.get(req.claim)
    if preset is not None and not satisfies(preset, req):
        return preset
    t = req.threshold
    if req.operator in (Operator.GT, Operator.GTE):
        return (t * Decimal("0.18488")).quantize(Decimal("0.01")) if t > 0 else t - abs(t) - 1
    if req.operator in (Operator.LT, Operator.LTE):
        return t * 2 + 1 if t >= 0 else t / 2
    if req.operator == Operator.EQ:
        return t + 1
    return t


class DemoLab:
    def __init__(self, app: Sufficia):
        self.app = app
        self.rt = app.rt

    def _require_demo(self) -> None:
        if not self.rt.settings.demo_mode:
            raise ApiError(404, "DEMO_DISABLED", "Demo Lab endpoints exist only in demo mode")

    # ---- market -----------------------------------------------------------------------------
    def market(self) -> dict[str, Any]:
        self._require_demo()
        from ..evidence.simulated_market import DEFAULT_VALUES

        return {"defaults": {k: str(v) for k, v in DEFAULT_VALUES.items()}, "overrides": self.rt.market.snapshot()}

    def set_market(self, asset: str, values: dict[str, Any]) -> dict[str, Any]:
        self._require_demo()
        clean: dict[str, Decimal] = {}
        for claim, raw in values.items():
            spec = get_claim(claim)
            if spec is None or not spec.numeric:
                raise ApiError(422, "UNKNOWN_CLAIM", f"{claim} is not a numeric claim")
            try:
                d = Decimal(str(raw))
            except Exception as exc:
                raise ApiError(422, "INVALID_VALUE", f"{claim}: {raw!r} is not a number") from exc
            if not d.is_finite():
                raise ApiError(422, "INVALID_VALUE", f"{claim}: value must be finite")
            clean[claim] = d
        for claim, d in clean.items():
            self.rt.market.set(asset, claim, d)
        self.app._audit("DEMO_MARKET_CHANGED", asset.upper(), {k: str(v) for k, v in clean.items()})
        return self.market()

    def reset_market(self, asset: Optional[str] = None) -> dict[str, Any]:
        self._require_demo()
        self.rt.market.reset(asset)
        self.app._audit("DEMO_MARKET_CHANGED", (asset or "*").upper(), {"reset": True})
        return self.market()

    # ---- injection ------------------------------------------------------------------------------
    def _make_item(
        self,
        contract: EvidenceContract,
        req: Requirement,
        *,
        condition: str,
        value: Any,
        observed_at: datetime,
        base: Optional[EvidenceItem],
    ) -> EvidenceItem:
        now = self.rt.now()
        spec = get_claim(req.claim)
        unit = spec.unit if spec else req.unit
        request = {"demo_lab": condition, "contract_id": contract.contract_id, "requirement_id": req.id,
                   "base_evidence_id": base.evidence_id if base else None}
        response = {"claim": req.claim, "value": value, "unit": unit, "observed_at": observed_at}
        rq = raw_hash(REQUEST_DOMAIN, canonical_bytes(request))
        rs = raw_hash(RESPONSE_DOMAIN, canonical_bytes(response))
        eid = "ev_inj_" + keccak(bytes.fromhex(rq[2:]) + bytes.fromhex(rs[2:]) + canonical_bytes(now)).hex()[:12]
        return EvidenceItem(
            evidence_id=eid,
            contract_id=contract.contract_id,
            requirement_id=req.id,
            claim=EvidenceClaim(name=req.claim, value=value, unit=unit),
            temporal=EvidenceTemporal(observed_at=observed_at, received_at=now),
            source=EvidenceSource(
                provider=DEMO_PROVIDER,
                source_type=SourceType.API,
                mode=SourceMode.INJECTED,
                endpoint_id=f"demo-lab:{condition}",
                source_family=DEMO_PROVIDER,
                network=base.source.network if base else None,
            ),
            integrity=EvidenceIntegrity(request_hash=rq, response_hash=rs),
            economic=EvidenceEconomic(cost=Decimal(0)),
        )

    def inject(
        self,
        condition: str,
        *,
        contract_id: Optional[str] = None,
        evidence_id: Optional[str] = None,
        requirement_id: Optional[str] = None,
    ) -> dict[str, Any]:
        self._require_demo()
        if condition == "missing":
            condition = "remove"
        if condition not in CONDITIONS:
            raise ApiError(422, "UNKNOWN_CONDITION", f"condition must be one of {', '.join(CONDITIONS)}")
        from ..db.models import EvidenceItemRow

        with self.rt.db.session() as s:
            if evidence_id:
                irow = s.get(EvidenceItemRow, evidence_id)
                if irow is None:
                    raise not_found("evidence", evidence_id)
                contract_id = irow.contract_id
            if not contract_id:
                raise ApiError(422, "CONTRACT_REQUIRED", "a contract_id or an evidence_id is required")
            _, contract = self.app._contract(s, contract_id)
            set_row = self.app._latest_set(s, contract_id)
            if set_row is None:
                raise ApiError(409, "EVIDENCE_NOT_ACQUIRED", "acquire evidence before injecting a condition")
            items = self.app._set_items(s, set_row)
            failures = self.app._set_failures(set_row)
            parent_id = set_row.id

        base: Optional[EvidenceItem] = None
        if evidence_id:
            matches = [i for i in items if i.evidence_id == evidence_id]
            if not matches:
                raise ApiError(409, "EVIDENCE_NOT_CURRENT", f"{evidence_id} is not in the current evidence set of {contract_id}")
            base = matches[0]
            requirement_id = base.requirement_id
        req = self._target_requirement(contract, condition, requirement_id)
        if base is None:
            same = [i for i in items if i.requirement_id == req.id]
            base = same[0] if same else None

        now = self.rt.now()
        new_items = list(items)
        if condition == "remove":
            new_items = [i for i in items if i.requirement_id != req.id] if base is None or not evidence_id else [
                i for i in items if i.evidence_id != base.evidence_id
            ]
            failures = [f for f in failures if f.requirement_id != req.id]
            detail = f"removed {req.id} evidence"
        elif condition == "conflict":
            if base is None:
                raise ApiError(409, "NO_BASE_EVIDENCE", f"no {req.id} observation to contradict")
            v = base.claim.value
            if not isinstance(v, (int, Decimal)) or isinstance(v, bool):
                raise ApiError(422, "CONDITION_NOT_APPLICABLE", "conflicts are injected on numeric observations")
            v = Decimal(v)
            other = (v / 8).quantize(Decimal("0.01")) if satisfies(v, req) or req.operator == Operator.EXISTS else v * 8
            if req.operator == Operator.EXISTS:
                other = (v * Decimal("1.5")).quantize(Decimal("0.0001"))
            new_items.append(self._make_item(contract, req, condition=condition, value=other, observed_at=now, base=base))
            detail = f"second {req.id} source reports {other} vs {v}"
        else:
            if condition == "valid":
                value, observed = _passing_value(req), now
            elif condition == "threshold":
                value, observed = _failing_value(req), now
            else:  # stale
                value = base.claim.value if base is not None else _passing_value(req)
                observed = now - timedelta(seconds=req.max_age_seconds + STALE_EXTRA_SECONDS)
            injected = self._make_item(contract, req, condition=condition, value=value, observed_at=observed, base=base)
            new_items = [i for i in items if i.requirement_id != req.id] + [injected]
            failures = [f for f in failures if f.requirement_id != req.id]
            detail = f"{req.id} := {value} observed {observed.isoformat()}"

        with self.rt.db.session() as s:
            row = self.app._store_set(
                s,
                contract_id=contract_id,
                items=new_items,
                failures=failures,
                origin="INJECTION",
                label=f"demo-lab:{condition} ({req.id})",
                parent_id=parent_id,
                acquisition={"spent": "0", "condition": condition, "requirement_id": req.id, "detail": detail},
            )
            new_id = row.id
        self.app._audit("EVIDENCE_INJECTED", contract_id, {"condition": condition, "requirement_id": req.id, "evidence_set_id": new_id, "detail": detail})
        view = self.app.get_evidence_set(new_id)
        view["injection"] = {"condition": condition, "requirement_id": req.id, "detail": detail}
        return view

    @staticmethod
    def _target_requirement(contract: EvidenceContract, condition: str, requirement_id: Optional[str]) -> Requirement:
        if requirement_id:
            req = contract.requirement(requirement_id)
            if req is None:
                raise not_found("requirement", requirement_id)
            return req
        by_claim = {r.claim: r for r in contract.requirements}
        if condition in ("stale", "remove"):
            return by_claim.get("asset_price") or contract.requirements[-1]
        if "available_liquidity" in by_claim:
            return by_claim["available_liquidity"]
        numeric = [r for r in contract.requirements if r.threshold is not None]
        return numeric[0] if numeric else contract.requirements[0]


class ScenarioRunner:
    def __init__(self, app: Sufficia, lab: DemoLab, scenarios_dir: str):
        self.app = app
        self.lab = lab
        self.dir = Path(scenarios_dir)

    def list(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            sc = json.loads(p.read_text())
            out.append({k: sc.get(k) for k in ("scenario_id", "version", "title", "description")} | {"steps": len(sc.get("steps", []))})
        return out

    def get(self, scenario_id: str) -> dict[str, Any]:
        if not scenario_id.replace("-", "").replace("_", "").isalnum():
            raise not_found("scenario", scenario_id)
        p = self.dir / f"{scenario_id}.json"
        if not p.is_file():
            raise not_found("scenario", scenario_id)
        return json.loads(p.read_text())

    def run(self, scenario_id: str, *, execute: bool = True, stop_before: Optional[str] = None) -> dict[str, Any]:
        self.lab._require_demo()
        sc = self.get(scenario_id)
        asset = sc.get("asset", "TOKEN-X")
        self.lab.reset_market(asset)
        if sc.get("market"):
            self.lab.set_market(asset, sc["market"])
        timeline: list[dict[str, Any]] = []
        started = self.app.rt.now()

        def step(entry: dict[str, Any]) -> None:
            entry["at"] = self.app.rt.now()
            timeline.append(entry)

        intent = self.app.create_intent(sc["intent"])
        intent_id = intent["intent"].intent_id
        step({"op": "intent", "intent_id": intent_id, "intent_hash": intent["intent_hash"], "ok": True})
        contract = self.app.compile(intent_id)
        contract_id = contract["contract"].contract_id
        step({"op": "compile", "contract_id": contract_id, "contract_hash": contract["contract_hash"],
              "requirements": contract["summary"].get("requirements"), "ok": True})

        last_decision: Optional[dict[str, Any]] = None
        last_receipt: Optional[str] = None
        passed = True
        for spec in sc.get("steps", []):
            op = spec["op"]
            if stop_before and op == stop_before:
                step({"op": op, "skipped": True, "reason": f"stopped before {stop_before}", "ok": True})
                break
            label = spec.get("label")
            if op == "acquire":
                ev = self.app.acquire(contract_id, sources_per_requirement=int(spec.get("sources_per_requirement", 1)))
                step({"op": op, "label": label, "evidence_set_id": ev["evidence_set_id"], "evidence_root": ev["evidence_root"],
                      "items": [{"evidence_id": v["item"].evidence_id, "requirement_id": v["item"].requirement_id,
                                 "value": v["item"].claim.value, "badge": v["badge"], "provider": v["item"].source.provider} for v in ev["items"]],
                      "payments": [{"provider": p["provider"], "status": p["status"], "amount": p["settled"] or p["quoted"]} for p in ev["payments"]],
                      "failures": ev["failures"], "ok": True})
            elif op == "market":
                self.lab.set_market(asset, spec["set"])
                step({"op": op, "label": label, "set": spec["set"], "ok": True})
            elif op == "inject":
                ev = self.lab.inject(spec["condition"], contract_id=contract_id, requirement_id=spec.get("requirement"))
                step({"op": op, "label": label, "evidence_set_id": ev["evidence_set_id"], "injection": ev["injection"], "ok": True})
            elif op == "decide":
                last_decision = self.app.decide(contract_id)
                d = last_decision["decision"]
                ok = spec.get("expect") in (None, d.state.value)
                passed &= ok
                rec = last_decision["receipts"][-1]["receipt_id"] if last_decision["receipts"] else None
                last_receipt = rec or last_receipt
                step({"op": op, "label": label, "decision_id": d.decision_id, "state": d.state, "expected": spec.get("expect"),
                      "reason_codes": d.reason_codes, "summary": d.summary, "authority": last_decision["authority"],
                      "receipt_id": rec, "ok": ok})
            elif op == "attempt_execute":
                assert last_decision is not None
                try:
                    self.app.execute_decision(last_decision["decision"].decision_id)
                    ok, err = False, None
                except ApiError as exc:
                    err = {"status": exc.status, "code": exc.code, "message": exc.message}
                    ok = exc.code == spec.get("expect_error", exc.code)
                passed &= ok
                step({"op": op, "label": label, "error": err, "ok": ok})
            elif op == "execute":
                if not execute:
                    step({"op": op, "label": label, "skipped": True, "reason": "execute=false", "ok": True})
                    continue
                assert last_decision is not None
                try:
                    res = self.app.execute_decision(last_decision["decision"].decision_id)
                    last_receipt = res.get("receipt_id") or last_receipt
                    step({"op": op, "label": label, "execution_id": res["execution_id"], "status": res["status"],
                          "tx_hash": res["tx_hash"], "authorization": {k: res["authorization"][k] for k in
                          ("authorization_id", "action_hash", "evidence_root", "expires_at", "authorize_tx_hash", "register_tx_hash")},
                          "transactions": res["transactions"], "receipt_id": res["receipt_id"], "ok": res["status"] == "EXECUTED"})
                    passed &= res["status"] == "EXECUTED"
                except ApiError as exc:
                    passed = False
                    step({"op": op, "label": label, "error": {"status": exc.status, "code": exc.code, "message": exc.message,
                                                               "detail": exc.detail}, "ok": False})
            elif op == "replay":
                if last_receipt is None:
                    step({"op": op, "label": label, "skipped": True, "reason": "no receipt", "ok": True})
                    continue
                rp = self.app.replay_receipt(last_receipt)
                ok = rp["result"] == spec.get("expect", "MATCH")
                passed &= ok
                step({"op": op, "label": label, "receipt_id": last_receipt, "result": rp["result"],
                      "chain_verification": rp["chain_verification"], "checks": len(rp["checks"]), "ok": ok})
            else:
                raise ApiError(500, "SCENARIO_INVALID", f"unknown scenario op {op}")

        final = last_decision["decision"].state if last_decision else None
        self.app._audit("DEMO_SCENARIO_RUN", scenario_id, {"contract_id": contract_id, "passed": passed, "final_state": getattr(final, "value", None)})
        return {
            "scenario_id": scenario_id,
            "version": sc.get("version"),
            "title": sc.get("title"),
            "description": sc.get("description"),
            "intent_id": intent_id,
            "contract_id": contract_id,
            "final_state": final,
            "receipt_id": last_receipt,
            "passed": passed,
            "started_at": started,
            "finished_at": self.app.rt.now(),
            "timeline": timeline,
        }
