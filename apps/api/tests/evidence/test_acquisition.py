"""Acquisition orchestration: concurrency, fallback, failure recording, two-source mode, timeouts."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from decimal import Decimal

import httpx

from sufficia.decision_engine import evaluate
from sufficia.domain.enums import DecisionState, PaymentStatus, SourceMode, SourceType
from sufficia.domain.reasons import ReasonCode
from sufficia.evidence.acquisition import AcquisitionResult, acquire, acquire_sync
from sufficia.evidence.adapters import build_registry, permitted_sources
from sufficia.evidence.base import AcquisitionError, SourceAdapter
from sufficia.evidence.planner import PlanStep, plan
from sufficia.evidence.simulated_market import MARKET


class StubAdapter(SourceAdapter):
    source_type = SourceType.API
    mode = SourceMode.LIVE

    def __init__(self, name, claims, *, value=Decimal("600000"), delay=0.0, error=None, family=None, h=None):
        self.name = name
        self.claims = frozenset(claims)
        self.value = value
        self.delay = delay
        self.error = error
        self.family = family or name
        self.calls = 0
        self.h = h

    async def fetch(self, requirement, ctx):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.h.raw_evidence(
            provider=self.name, claim=requirement.claim, value=self.value, source_family=self.family, observed_at=ctx.now(), received_at=ctx.now(),
            response_body=f'{{"v":"{self.value}","p":"{self.name}"}}'.encode(),
        )


def test_requirements_are_fetched_concurrently(h):
    contract = h.make_contract()
    registry = {
        "a": StubAdapter("a", {"smart_money_inflow_24h"}, delay=0.3, value=Decimal("2000000"), h=h),
        "b": StubAdapter("b", {"available_liquidity"}, delay=0.3, h=h),
        "c": StubAdapter("c", {"asset_price"}, delay=0.3, value=Decimal("0.4"), h=h),
    }
    steps = [PlanStep("r_flow", "smart_money_inflow_24h", ["a"]), PlanStep("r_liq", "available_liquidity", ["b"]), PlanStep("r_price", "asset_price", ["c"])]
    t0 = time.perf_counter()
    result = h.run(acquire(contract, steps, h.make_ctx(contract), registry))
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.75, elapsed  # sequential would be >= 0.9s
    assert isinstance(result, AcquisitionResult)
    assert [i.requirement_id for i in result.items] == ["r_flow", "r_liq", "r_price"]
    assert result.failures == [] and result.spent == Decimal(0)
    assert result.started_at <= result.finished_at
    events = [e["event"] for e in result.timeline]
    assert events[0] == "acquisition.started" and events[-1] == "acquisition.finished"
    assert events.count("source.succeeded") == 3
    for e in result.timeline:
        assert {"event", "requirement_id", "provider", "at", "detail"} <= set(e)


def test_fallback_records_every_failed_attempt(h):
    contract = h.make_contract(h.req("available_liquidity", "r_liq"))
    primary = StubAdapter("primary", {"available_liquidity"}, error=AcquisitionError(ReasonCode.RATE_LIMITED, "429"), h=h)
    secondary = StubAdapter("secondary", {"available_liquidity"}, error=AcquisitionError(ReasonCode.INVALID_RESPONSE, "junk", cost_spent=Decimal("0.01")), h=h)
    tertiary = StubAdapter("tertiary", {"available_liquidity"}, h=h)
    registry = {"p": primary, "s": secondary, "t": tertiary}
    result = h.run(acquire(contract, [PlanStep("r_liq", "available_liquidity", ["p", "s", "t"])], h.make_ctx(contract), registry))
    assert [i.source.provider for i in result.items] == ["tertiary"]
    assert [(f.provider, f.code) for f in result.failures] == [("primary", ReasonCode.RATE_LIMITED), ("secondary", ReasonCode.INVALID_RESPONSE)]
    assert result.failures[1].cost_spent == Decimal("0.01")
    assert all(f.acquisition_id and f.acquisition_id.startswith("acq_") for f in result.failures)
    assert result.spent == Decimal("0.01")
    assert tertiary.calls == 1


def test_stops_at_first_success_in_single_source_mode(h):
    contract = h.make_contract(h.req("available_liquidity", "r_liq"))
    a, b = StubAdapter("a", {"available_liquidity"}, h=h), StubAdapter("b", {"available_liquidity"}, h=h)
    result = h.run(acquire(contract, [PlanStep("r_liq", "available_liquidity", ["a", "b"])], h.make_ctx(contract), {"a": a, "b": b}))
    assert len(result.items) == 1 and a.calls == 1 and b.calls == 0


def test_two_source_mode_enables_conflict_detection(h):
    contract = h.make_contract(h.req("available_liquidity", "r_liq", operator=">=", threshold=Decimal("100000")))
    a = StubAdapter("a", {"available_liquidity"}, value=Decimal("600000"), h=h)
    failing = StubAdapter("f", {"available_liquidity"}, error=AcquisitionError(ReasonCode.SOURCE_UNAVAILABLE, "down"), h=h)
    b = StubAdapter("b", {"available_liquidity"}, value=Decimal("150000"), h=h)
    c = StubAdapter("c", {"available_liquidity"}, h=h)
    ctx = h.make_ctx(contract)
    result = h.run(acquire(contract, [PlanStep("r_liq", "available_liquidity", ["a", "f", "b", "c"])], ctx, {"a": a, "f": failing, "b": b, "c": c}, sources_per_requirement=2))
    assert [i.source.provider for i in result.items] == ["a", "b"]
    assert c.calls == 0 and len(result.failures) == 1
    decision = evaluate(contract, result.evidence_set(contract.contract_id), ctx.now() + timedelta(seconds=1))
    assert decision.state == DecisionState.ESCALATE
    assert ReasonCode.CRITICAL_SOURCE_CONFLICT in decision.reason_codes


def test_claim_without_source_reports_source_not_configured(h):
    settings = h.make_settings(evidence_profile="live")  # nothing configured
    contract = h.make_contract()
    registry = build_registry(settings)
    ctx = h.make_ctx(contract, settings)
    result = h.run(acquire(contract, plan(contract, settings, registry), ctx, registry))
    assert result.items == []
    assert {f.requirement_id for f in result.failures} == {"r_flow", "r_liq", "r_price"}
    assert all(f.code == ReasonCode.SOURCE_NOT_CONFIGURED and f.provider == "planner" for f in result.failures)
    assert "NOT_CONFIGURED" in result.failures[0].detail
    decision = evaluate(contract, result.evidence_set(contract.contract_id), ctx.now())
    assert decision.state == DecisionState.ABSTAIN


def test_slow_source_times_out_and_falls_back(h):
    contract = h.make_contract(h.req("asset_price", "r_price"))
    settings = h.make_settings(http_timeout_seconds=0.05)
    slow = StubAdapter("slow", {"asset_price"}, delay=5, h=h)
    fast = StubAdapter("fast", {"asset_price"}, value=Decimal("0.42"), h=h)
    t0 = time.perf_counter()
    result = h.run(acquire(contract, [PlanStep("r_price", "asset_price", ["slow", "fast"])], h.make_ctx(contract, settings), {"slow": slow, "fast": fast}))
    assert time.perf_counter() - t0 < 3
    assert result.failures[0].provider == "slow" and result.failures[0].code == ReasonCode.SOURCE_UNAVAILABLE
    assert result.items[0].source.provider == "fast"


def test_adapter_bug_fails_closed_and_visible(h):
    contract = h.make_contract(h.req("asset_price", "r_price"))
    buggy = StubAdapter("buggy", {"asset_price"}, error=KeyError("boom"), h=h)
    result = h.run(acquire(contract, [PlanStep("r_price", "asset_price", ["buggy"])], h.make_ctx(contract), {"buggy": buggy}))
    assert result.items == [] and result.failures[0].code == ReasonCode.INVALID_RESPONSE
    assert "KeyError" in result.failures[0].detail


def test_demo_profile_end_to_end_with_real_x402_reference_payment(h):
    """demo profile: scenario + paid x402 liquidity from the local reference server (in-process)."""
    from sufficia.devtools.x402_reference import create_x402_reference_app

    settings = h.make_settings(evidence_profile="demo")
    app = create_x402_reference_app(settings)
    contract = h.make_contract(budget="0.25")
    registry = build_registry(settings)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://x402-reference.local") as ref:
            ctx = h.make_ctx(contract, settings, reference_http=ref)
            result = await acquire(contract, plan(contract, settings, registry), ctx, registry)
            return ctx, result

    ctx, result = h.run(go())
    assert result.failures == []
    liq = next(i for i in result.items if i.requirement_id == "r_liq")
    assert liq.source.provider == "x402-reference" and liq.source.mode == SourceMode.SIMULATED
    assert liq.economic.cost == Decimal("0.02") and liq.economic.payment.status == PaymentStatus.SIMULATED
    assert liq.claim.value == Decimal("92440")
    assert len(result.payments) == 1
    record = result.payments[0]
    assert record.evidence_id == liq.evidence_id and record.requirement_id == "r_liq"
    assert record.network == "eip155:10143" and record.scheme == "exact" and record.status == PaymentStatus.SIMULATED
    assert record.quoted == Decimal("0.02") and record.settled is None
    assert record.pay_to.lower() == settings.x402_reference_pay_to.lower()
    assert result.spent == Decimal("0.02") == ctx.budget.spent
    assert app.state.x402.verified_payments == 1
    assert any(e["event"] == "payment.simulated" for e in result.timeline)
    decision = evaluate(contract, result.evidence_set(contract.contract_id), ctx.now() + timedelta(seconds=1), permitted_sources=permitted_sources(registry, settings))
    assert decision.state == DecisionState.AUTHORIZE  # 50k threshold in the test contract
    assert decision.evidence_cost_usd == Decimal("0.02")


def test_demo_profile_falls_back_to_scenario_when_budget_is_zero(h):
    from sufficia.devtools.x402_reference import create_x402_reference_app
    from sufficia.evidence.base import EvidenceBudget

    settings = h.make_settings(evidence_profile="demo")
    app = create_x402_reference_app(settings)
    contract = h.make_contract()
    registry = build_registry(settings)
    MARKET.set("TOKEN-X", "available_liquidity", Decimal("700000"))

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://x402-reference.local") as ref:
            ctx = h.make_ctx(contract, settings, reference_http=ref, budget=EvidenceBudget(Decimal("0.01")))
            return await acquire(contract, plan(contract, settings, registry), ctx, registry)

    result = h.run(go())
    liq = next(i for i in result.items if i.requirement_id == "r_liq")
    assert liq.source.provider == "simulated-market" and liq.claim.value == Decimal("700000")
    failure = next(f for f in result.failures if f.requirement_id == "r_liq")
    assert failure.code == ReasonCode.BUDGET_EXCEEDED and failure.cost_spent == 0
    assert [p.status for p in result.payments] == [PaymentStatus.BUDGET_EXCEEDED]
    assert app.state.x402.verified_payments == 0


def test_acquire_sync_outside_and_inside_a_loop(h):
    contract = h.make_contract(h.req("asset_price", "r_price"))
    registry = {"a": StubAdapter("a", {"asset_price"}, value=Decimal("0.4"), h=h)}
    steps = [PlanStep("r_price", "asset_price", ["a"])]
    result = acquire_sync(contract, steps, h.make_ctx(contract), registry)
    assert len(result.items) == 1

    async def inside():
        return acquire_sync(contract, steps, h.make_ctx(contract), registry)

    assert len(h.run(inside()).items) == 1


def test_timeline_is_json_friendly_enough_for_the_ui(h):
    contract = h.make_contract(h.req("asset_price", "r_price"))
    result = h.run(acquire(contract, [PlanStep("r_price", "asset_price", [])], h.make_ctx(contract), {}))
    json.dumps(result.timeline, default=str)
    assert result.failures[0].code == ReasonCode.SOURCE_NOT_CONFIGURED
