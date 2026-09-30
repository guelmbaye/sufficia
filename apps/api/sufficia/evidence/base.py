"""Evidence acquisition interfaces (Document 02 §27, Document 03 §14-15).

Source adapters are *untrusted input boundaries*: they return a RawEvidence observation or raise
AcquisitionError. They never return PASS/FAIL — the Decision Engine alone owns policy outcomes.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable, Optional

from ..domain.enums import SourceMode, SourceType
from ..domain.models import ChainReference, EvidenceContract, EvidenceDerivation, EvidencePayment, Requirement
from ..domain.reasons import ReasonCode

if TYPE_CHECKING:  # pragma: no cover
    import httpx

    from ..config import Settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AcquisitionError(Exception):
    """A source could not produce an observation. Maps to ABSTAIN if the claim is mandatory."""

    def __init__(self, code: ReasonCode, detail: str = "", cost_spent: Decimal = Decimal(0), payment: Any = None):
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail
        self.cost_spent = cost_spent
        self.payment = payment


class EvidenceBudget:
    """Pre-authorized operational budget for evidence acquisition (Document 04 §41-42).

    Thread/async-safe. `try_reserve` is called *before* any payment is signed; a quote that does
    not fit is never signed (EVIDENCE_BUDGET_EXCEEDED).
    """

    def __init__(self, limit: Decimal, max_per_request: Decimal | None = None, already_spent: Decimal = Decimal(0)):
        self.limit = Decimal(limit)
        self.max_per_request = max_per_request
        self._spent = Decimal(already_spent)
        self._reserved = Decimal(0)
        self._lock = asyncio.Lock()

    @property
    def spent(self) -> Decimal:
        return self._spent

    @property
    def remaining(self) -> Decimal:
        return self.limit - self._spent - self._reserved

    async def try_reserve(self, amount: Decimal) -> bool:
        async with self._lock:
            amount = Decimal(amount)
            if amount < 0:
                return False
            if self.max_per_request is not None and amount > self.max_per_request:
                return False
            if amount > self.limit - self._spent - self._reserved:
                return False
            self._reserved += amount
            return True

    async def commit(self, reserved: Decimal, actual: Decimal | None = None) -> None:
        async with self._lock:
            self._reserved -= Decimal(reserved)
            self._spent += Decimal(reserved if actual is None else actual)

    async def release(self, reserved: Decimal) -> None:
        async with self._lock:
            self._reserved -= Decimal(reserved)


@dataclass
class AcquisitionContext:
    contract: EvidenceContract
    run_id: str
    settings: "Settings"
    budget: EvidenceBudget
    http: "httpx.AsyncClient"
    now: Callable[[], datetime] = utcnow
    # In-process transport to the local x402 reference server (no network needed).
    reference_http: Optional["httpx.AsyncClient"] = None
    # Scenario-level overrides for simulated sources (asset -> claim -> value).
    scenario_id: Optional[str] = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def asset(self) -> str:
        return self.contract.action.asset

    def acquisition_id(self, requirement_id: str, provider: str) -> str:
        from ..canonical.hashes import REQUEST_DOMAIN, raw_hash

        return "acq_" + raw_hash(REQUEST_DOMAIN, f"{self.run_id}|{requirement_id}|{provider}".encode())[2:18]


@dataclass
class RawEvidence:
    """One observation exactly as a provider produced it, plus provenance."""

    provider: str
    source_type: SourceType
    mode: SourceMode
    claim: str
    value: Any
    unit: Optional[str]
    observed_at: Optional[datetime | str]
    received_at: datetime
    request_descriptor: dict[str, Any]  # method, url, body/params (no secrets) -> request_hash
    response_body: bytes  # exact bytes received -> response_hash
    endpoint_id: Optional[str] = None
    upstream_source: Optional[str] = None
    source_family: Optional[str] = None
    network: Optional[str] = None
    chain_ref: Optional[ChainReference] = None
    captured_at: Optional[datetime] = None
    cost: Decimal = Decimal(0)
    payment: Optional[EvidencePayment] = None
    derivation: Optional[EvidenceDerivation] = None


class SourceAdapter(ABC):
    """A registered evidence source. `name` is the provider identity recorded in provenance."""

    name: str
    source_type: SourceType
    mode: SourceMode
    claims: frozenset[str]
    paid: bool = False

    def is_configured(self, settings: "Settings") -> bool:  # pragma: no cover - overridden
        return True

    @abstractmethod
    async def fetch(self, requirement: Requirement, ctx: AcquisitionContext) -> RawEvidence: ...
