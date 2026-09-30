"""SUFFICIA SDK — the single interface an agent needs (Document 02 §40).

    from sufficia_sdk import EvidenceGate

    gate = EvidenceGate("http://localhost:8000")
    decision = gate.evaluate("Buy up to 100 USDC of TOKEN-X only if smart-money inflows exceed $1M "
                             "and liquidity exceeds $500K.")
    if decision.state == "AUTHORIZE":
        execution = gate.execute(decision)
        print(execution["tx_hash"], gate.replay(execution["receipt_id"])["result"])
    else:
        print(decision.state, decision.reason_codes, decision.summary)   # money did not move

The SDK is a thin HTTP client: all authority stays server-side and onchain. It never retries an
execution blindly: executions are idempotent per authorization on the server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

__all__ = ["Decision", "EvidenceGate", "SufficiaError"]
__version__ = "1.0.0"


class SufficiaError(Exception):
    def __init__(self, status: int, code: str, message: str, detail: Optional[dict] = None):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message, self.detail = status, code, message, detail or {}


@dataclass
class Decision:
    decision_id: str
    state: str
    reason_codes: list[str]
    summary: str
    contract_id: str
    contract_hash: str
    evidence_root: str
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def authorized(self) -> bool:
        return self.state == "AUTHORIZE"


class EvidenceGate:
    def __init__(self, base_url: str = "http://localhost:8000", *, api_key: Optional[str] = None, timeout: float = 60.0,
                 client: Optional[httpx.Client] = None):
        headers = {"X-API-Key": api_key} if api_key else {}
        self._http = client or httpx.Client(base_url=base_url.rstrip("/") + "/api/v1", headers=headers, timeout=timeout)

    # ---- low level -------------------------------------------------------------------------
    def _call(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        r = self._http.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                err = r.json()["error"]
            except Exception:
                err = {"code": "HTTP_ERROR", "message": r.text}
            raise SufficiaError(r.status_code, err.get("code", "HTTP_ERROR"), err.get("message", ""), err.get("detail"))
        return r.json()

    # ---- flow ----------------------------------------------------------------------------------
    def compile(self, intent: str, *, mode: Optional[str] = None) -> dict[str, Any]:
        """Creates the intent and freezes its Evidence Contract."""
        created = self._call("POST", "/intents", json={"text": intent})
        body = {"mode": mode} if mode else None
        return self._call("POST", f"/intents/{created['intent']['intent_id']}/compile", json=body)

    def acquire(self, contract_id: str, *, sources_per_requirement: int = 1) -> dict[str, Any]:
        return self._call("POST", f"/contracts/{contract_id}/acquire", json={"sources_per_requirement": sources_per_requirement})

    def decide(self, contract_id: str) -> Decision:
        d = self._call("POST", f"/contracts/{contract_id}/decide")
        dec = d["decision"]
        return Decision(dec["decision_id"], dec["state"], dec["reason_codes"], dec["summary"], dec["contract_id"],
                        dec["contract_hash"], dec["evidence_root"], d)

    def evaluate(self, intent: str, *, contract_id: Optional[str] = None) -> Decision:
        """intent -> frozen contract -> evidence -> deterministic decision."""
        if contract_id is None:
            contract_id = self.compile(intent)["contract"]["contract_id"]
        self.acquire(contract_id)
        return self.decide(contract_id)

    def execute(self, decision: Decision | str) -> dict[str, Any]:
        """Authorize + protected execution. Raises SufficiaError(403, EVIDENCE_AUTHORIZATION_REQUIRED) unless AUTHORIZE."""
        decision_id = decision if isinstance(decision, str) else decision.decision_id
        return self._call("POST", f"/decisions/{decision_id}/execute")

    def receipt(self, receipt_id: str) -> dict[str, Any]:
        return self._call("GET", f"/receipts/{receipt_id}")

    def replay(self, receipt_id: str) -> dict[str, Any]:
        return self._call("POST", f"/receipts/{receipt_id}/replay")
