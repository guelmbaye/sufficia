"""Evidence acquisition: execute a plan, normalize observations, record every failure and payment.

* Requirements are fetched concurrently (``asyncio.gather``); within one requirement the planned
  sources are tried in order (primary, then fallbacks) until ``sources_per_requirement`` of them have
  produced an observation. ``sources_per_requirement=2`` collects from two different adapters when
  available, which is what lets the engine detect source conflicts.
* Every failed attempt becomes an :class:`AcquisitionFailure` (code, provider, detail, cost_spent),
  including attempts that paid and still failed. A requirement with no usable source gets a
  SOURCE_NOT_CONFIGURED failure. Nothing is ever silently dropped.
* Every quote/payment seen becomes a :class:`PaymentRecord` — successful, rejected or refused by the
  budget gate — linked to the evidence item when one was produced.
* Each attempt is bounded by a per-source timeout (``settings.http_timeout_seconds``; paid sources
  get room for the quote round-trip plus the same-payload retries of the x402 client). A paid attempt
  cancelled after its signed authorization left the process is recorded with its cost (conservative:
  the money may have moved).

Acquisition never decides anything: items go to the Decision Engine exactly as normalized.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from ..domain.enums import PaymentStatus
from ..domain.models import AcquisitionFailure, EvidenceContract, EvidenceItem, EvidencePayment, EvidenceSet, Requirement
from ..domain.reasons import ReasonCode
from ..payments.x402.client import SIGNED_STORE_KEY
from .base import AcquisitionContext, AcquisitionError, RawEvidence, SourceAdapter
from .normalizer import make_evidence_id, normalize, response_hash
from .planner import PlanStep

PLANNER_PROVIDER = "planner"


@dataclass
class PaymentRecord:
    acquisition_id: str
    provider: str
    requirement_id: str
    evidence_id: Optional[str]
    protocol: str
    network: Optional[str]
    scheme: Optional[str]
    quoted: Optional[Decimal]
    settled: Optional[Decimal]
    currency: str
    status: PaymentStatus
    payment_reference: Optional[str]
    payer: Optional[str]
    pay_to: Optional[str]
    created_at: datetime
    error: Optional[str] = None

    @classmethod
    def from_payment(
        cls,
        payment: EvidencePayment,
        *,
        acquisition_id: str,
        provider: str,
        requirement_id: str,
        evidence_id: Optional[str],
        created_at: datetime,
        error: Optional[str] = None,
    ) -> "PaymentRecord":
        return cls(
            acquisition_id=acquisition_id,
            provider=provider,
            requirement_id=requirement_id,
            evidence_id=evidence_id,
            protocol=payment.protocol,
            network=payment.network,
            scheme=payment.scheme,
            quoted=payment.quoted_amount,
            settled=payment.settled_amount,
            currency=payment.currency,
            status=payment.status,
            payment_reference=payment.payment_reference,
            payer=payment.payer,
            pay_to=payment.pay_to,
            created_at=created_at,
            error=error,
        )


@dataclass
class AcquisitionResult:
    items: list[EvidenceItem]
    failures: list[AcquisitionFailure]
    payments: list[PaymentRecord]
    spent: Decimal  # sum of item costs + cost of failed paid attempts (same basis as the engine)
    started_at: datetime
    finished_at: datetime
    timeline: list[dict[str, Any]] = field(default_factory=list)

    def evidence_set(self, contract_id: str) -> EvidenceSet:
        return EvidenceSet(contract_id=contract_id, items=list(self.items), acquisition_failures=list(self.failures))


@dataclass
class _ReqOutcome:
    items: list[EvidenceItem] = field(default_factory=list)
    failures: list[AcquisitionFailure] = field(default_factory=list)
    payments: list[PaymentRecord] = field(default_factory=list)


def source_timeout(adapter: SourceAdapter, settings: Any) -> float:
    """Wall-clock bound of one attempt. Paid sources: quote + (1 + retries) paid sends."""
    base = float(getattr(settings, "http_timeout_seconds", 6.0))
    if getattr(adapter, "paid", False):
        retries = int(getattr(settings, "x402_send_retries", 1))
        return base * (2 + retries) + 1.0
    return base + 1.0


async def acquire(
    contract: EvidenceContract,
    plan: list[PlanStep],
    ctx: AcquisitionContext,
    registry: dict[str, SourceAdapter],
    *,
    sources_per_requirement: int = 1,
) -> AcquisitionResult:
    started_at = ctx.now()
    timeline: list[dict[str, Any]] = []
    wanted = max(1, int(sources_per_requirement))

    def event(name: str, requirement_id: Optional[str], provider: Optional[str], detail: str = "", **extra: Any) -> None:
        entry: dict[str, Any] = {"event": name, "requirement_id": requirement_id, "provider": provider, "at": ctx.now(), "detail": detail}
        entry.update(extra)
        timeline.append(entry)

    event("acquisition.started", None, None, f"{len(plan)} requirement(s), {wanted} source(s) each")

    async def run_step(step: PlanStep) -> _ReqOutcome:
        outcome = _ReqOutcome()
        requirement = contract.requirement(step.requirement_id)
        if requirement is None:
            event("requirement.unknown", step.requirement_id, None, "plan step does not match a contract requirement")
            return outcome
        event("requirement.started", requirement.id, None, f"claim {requirement.claim}", sources=list(step.adapters))
        if not step.adapters:
            skipped = ", ".join(f"{n}={r}" for n, r in step.skipped) or "none"
            detail = f"no configured source for claim '{requirement.claim}' (skipped: {skipped})"
            outcome.failures.append(
                AcquisitionFailure(requirement_id=requirement.id, provider=PLANNER_PROVIDER, code=ReasonCode.SOURCE_NOT_CONFIGURED, detail=detail)
            )
            event("source.failed", requirement.id, PLANNER_PROVIDER, detail, code=ReasonCode.SOURCE_NOT_CONFIGURED.value)
            return outcome

        for name in step.adapters:
            if len(outcome.items) >= wanted:
                break
            adapter = registry.get(name)
            if adapter is None:
                detail = f"source '{name}' is not registered"
                outcome.failures.append(
                    AcquisitionFailure(requirement_id=requirement.id, provider=name, code=ReasonCode.SOURCE_NOT_CONFIGURED, detail=detail)
                )
                event("source.failed", requirement.id, name, detail, code=ReasonCode.SOURCE_NOT_CONFIGURED.value)
                continue
            await _attempt(adapter, requirement, outcome)
        event("requirement.finished", requirement.id, None, f"{len(outcome.items)} item(s), {len(outcome.failures)} failure(s)")
        return outcome

    async def _attempt(adapter: SourceAdapter, requirement: Requirement, outcome: _ReqOutcome) -> None:
        acquisition_id = ctx.acquisition_id(requirement.id, adapter.name)
        timeout = source_timeout(adapter, ctx.settings)
        event("source.attempt", requirement.id, adapter.name, f"mode {adapter.mode.value}", acquisition_id=acquisition_id)
        try:
            raw = await asyncio.wait_for(adapter.fetch(requirement, ctx), timeout=timeout)
        except AcquisitionError as err:
            _fail(outcome, requirement, adapter, acquisition_id, err.code, err.detail, Decimal(err.cost_spent), err.payment)
            return
        except asyncio.TimeoutError:
            cost, payment = _abandoned_payment(acquisition_id)
            _fail(outcome, requirement, adapter, acquisition_id, ReasonCode.SOURCE_UNAVAILABLE, f"no answer within {timeout:g}s", cost, payment)
            return
        except Exception as exc:  # noqa: BLE001 - an adapter bug must fail closed, visibly
            cost, payment = _abandoned_payment(acquisition_id)
            _fail(outcome, requirement, adapter, acquisition_id, ReasonCode.INVALID_RESPONSE, f"adapter error: {type(exc).__name__}: {exc}", cost, payment)
            return

        try:
            evidence_id = make_evidence_id(acquisition_id, response_hash(raw))
            item = normalize(raw, requirement=requirement, contract_id=contract.contract_id, evidence_id=evidence_id, acquisition_id=acquisition_id)
        except Exception as exc:  # noqa: BLE001 - e.g. an adapter produced an unrepresentable field
            _fail(
                outcome,
                requirement,
                adapter,
                acquisition_id,
                ReasonCode.INVALID_RESPONSE,
                f"observation could not be normalized: {type(exc).__name__}",
                Decimal(raw.cost),
                raw.payment,
            )
            return
        outcome.items.append(item)
        if raw.payment is not None:
            _record_payment(outcome, raw.payment, requirement, adapter, acquisition_id, item.evidence_id, None)
        event(
            "source.succeeded",
            requirement.id,
            adapter.name,
            f"{item.source.provider} {item.source.mode.value}",
            evidence_id=item.evidence_id,
            acquisition_id=acquisition_id,
            cost=str(item.economic.cost),
        )

    def _abandoned_payment(acquisition_id: str) -> tuple[Decimal, Optional[EvidencePayment]]:
        """A cancelled paid attempt whose signed authorization was sent: count it (money may have moved)."""
        signed = ctx.extras.get(SIGNED_STORE_KEY, {}).get(acquisition_id)
        if signed is None or not signed.sent:
            return Decimal(0), None
        return signed.claim_cost(), signed.evidence_payment(PaymentStatus.TIMEOUT)

    def _fail(
        outcome: _ReqOutcome,
        requirement: Requirement,
        adapter: SourceAdapter,
        acquisition_id: str,
        code: ReasonCode,
        detail: str,
        cost: Decimal,
        payment: Optional[EvidencePayment],
    ) -> None:
        outcome.failures.append(
            AcquisitionFailure(
                requirement_id=requirement.id,
                provider=adapter.name,
                code=code,
                detail=detail,
                cost_spent=cost,
                acquisition_id=acquisition_id,
            )
        )
        if payment is not None:
            _record_payment(outcome, payment, requirement, adapter, acquisition_id, None, detail)
        event("source.failed", requirement.id, adapter.name, detail, code=code.value, cost=str(cost), acquisition_id=acquisition_id)

    def _record_payment(
        outcome: _ReqOutcome,
        payment: EvidencePayment,
        requirement: Requirement,
        adapter: SourceAdapter,
        acquisition_id: str,
        evidence_id: Optional[str],
        error: Optional[str],
    ) -> None:
        record = PaymentRecord.from_payment(
            payment,
            acquisition_id=acquisition_id,
            provider=adapter.name,
            requirement_id=requirement.id,
            evidence_id=evidence_id,
            created_at=ctx.now(),
            error=error,
        )
        outcome.payments.append(record)
        event(
            "payment." + record.status.value.lower(),
            requirement.id,
            adapter.name,
            f"{record.quoted} {record.currency} on {record.network}",
            payment_reference=record.payment_reference,
            evidence_id=evidence_id,
        )

    outcomes = await asyncio.gather(*(run_step(step) for step in plan))

    items: list[EvidenceItem] = []
    failures: list[AcquisitionFailure] = []
    payments: list[PaymentRecord] = []
    for o in outcomes:
        items.extend(o.items)
        failures.extend(o.failures)
        payments.extend(o.payments)
    spent = sum((Decimal(i.economic.cost) for i in items), Decimal(0)) + sum((Decimal(f.cost_spent) for f in failures), Decimal(0))
    finished_at = ctx.now()
    event("acquisition.finished", None, None, f"{len(items)} item(s), {len(failures)} failure(s), spent {spent} USDC")
    return AcquisitionResult(
        items=items,
        failures=failures,
        payments=payments,
        spent=spent,
        started_at=started_at,
        finished_at=finished_at,
        timeline=timeline,
    )


def acquire_sync(
    contract: EvidenceContract,
    plan: list[PlanStep],
    ctx: AcquisitionContext,
    registry: dict[str, SourceAdapter],
    *,
    sources_per_requirement: int = 1,
) -> AcquisitionResult:
    """Synchronous wrapper. Uses ``asyncio.run`` when no loop is running in this thread; otherwise runs
    the acquisition on a fresh loop in a worker thread (async callers should ``await acquire`` instead).
    Note: the httpx clients on ``ctx`` must not be shared with another event loop concurrently.
    """
    coro_factory = lambda: acquire(contract, plan, ctx, registry, sources_per_requirement=sources_per_requirement)  # noqa: E731
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro_factory())).result()


__all__ = ["AcquisitionResult", "PaymentRecord", "RawEvidence", "acquire", "acquire_sync", "source_timeout"]
