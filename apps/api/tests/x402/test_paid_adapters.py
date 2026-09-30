"""x402-paid adapters: payment recorded on the evidence; payment never makes evidence valid."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from sufficia.decision_engine import evaluate
from sufficia.devtools.x402_reference import create_x402_reference_app
from sufficia.domain.enums import DecisionState, EvidenceStatus, PaymentStatus, SourceMode, SourceType
from sufficia.domain.reasons import ReasonCode
from sufficia.evidence.acquisition import acquire
from sufficia.evidence.adapters import NansenSmartMoneyAdapter, X402PaidEvidenceAdapter, X402ReferenceAdapter
from sufficia.evidence.adapters.registry import permitted_sources
from sufficia.evidence.base import AcquisitionError, EvidenceBudget
from sufficia.evidence.planner import PlanStep
from sufficia.payments.x402 import PAYMENT_REQUIRED_HEADER, PAYMENT_SIGNATURE_HEADER, SIGNED_STORE_KEY, b64_json_encode

USDC = "0x534b2f3A21130d7a60830c2Df862319e593943A3"
PAY_TO = "0x000000000000000000000000000000000000dEaD"
PAID_URL = "https://paid.test/v1/evidence"


def _required(amount="30000"):
    return {
        "x402Version": 2,
        "accepts": [
            {"scheme": "exact", "network": "eip155:10143", "amount": amount, "asset": USDC, "payTo": PAY_TO, "maxTimeoutSeconds": 60, "extra": {"name": "USDC", "version": "2"}}
        ],
    }


def paid_endpoint(body_for_paid, *, amount="30000", record=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(request)
        if PAYMENT_SIGNATURE_HEADER not in request.headers:
            required = _required(amount)
            return httpx.Response(402, json=required, headers={PAYMENT_REQUIRED_HEADER: b64_json_encode(required)})
        return body_for_paid(request)

    return handler


def _paid_settings(h, **kw):
    return h.make_settings(x402_evidence_url=PAID_URL, asset_token_address=h.TOKEN, **kw)


def test_live_paid_adapter_happy_path(h):
    seen: list[httpx.Request] = []
    body = {"claim": "available_liquidity", "value": "812345.5", "unit": "USD", "observed_at": "2026-09-30T11:59:58Z", "asset": "TOKEN-X", "provider": "acme-data", "upstream_source": "kuru:pool:0xabc"}
    ctx = h.make_ctx(settings=_paid_settings(h), http=h.mock_http(paid_endpoint(lambda r: httpx.Response(200, json=body), record=seen)))
    adapter = X402PaidEvidenceAdapter(ctx.settings)
    assert adapter.is_configured(ctx.settings) and adapter.paid
    raw = h.run(adapter.fetch(h.req("available_liquidity"), ctx))
    assert raw.provider == "x402-paid-evidence" and raw.mode == SourceMode.LIVE and raw.source_type == SourceType.API
    assert raw.source_family == "x402:paid.test"
    assert raw.upstream_source == "kuru:pool:0xabc"
    assert raw.value == "812345.5"
    assert raw.observed_at.isoformat() == "2026-09-30T11:59:58+00:00"
    assert raw.cost == Decimal("0.03")
    assert raw.payment.status == PaymentStatus.VERIFIED and raw.payment.quoted_amount == Decimal("0.03")
    assert raw.payment.network == "eip155:10143" and raw.payment.pay_to == PAY_TO
    assert dict(seen[0].url.params) == {"claim": "available_liquidity", "asset": "TOKEN-X", "token": h.TOKEN}
    assert ctx.budget.spent == Decimal("0.03")


def test_live_paid_adapter_not_configured_without_url(h):
    assert not X402PaidEvidenceAdapter().is_configured(h.make_settings())
    assert not X402PaidEvidenceAdapter().is_configured(_paid_settings(h, x402_enabled=False))


def test_paid_but_malformed_evidence_is_kept_and_payment_recorded(h):
    """The provider was paid and returned '1,340,000': the item exists (engine marks it INVALID),
    the payment is recorded, the cost counts, and the decision is ABSTAIN — never AUTHORIZE."""
    body = {"claim": "available_liquidity", "value": "1,340,000", "unit": "USD", "observed_at": "2026-09-30T11:59:58Z", "asset": "TOKEN-X", "provider": "acme"}
    settings = _paid_settings(h)
    contract = h.make_contract(h.req("available_liquidity", "r_liq", operator=">=", threshold=Decimal("500000")))
    ctx = h.make_ctx(contract, settings, http=h.mock_http(paid_endpoint(lambda r: httpx.Response(200, json=body))))
    registry = {"x402_paid": X402PaidEvidenceAdapter(settings)}
    result = h.run(acquire(contract, [PlanStep("r_liq", "available_liquidity", ["x402_paid"])], ctx, registry))
    assert len(result.items) == 1 and result.failures == []
    item = result.items[0]
    assert item.claim.value == "1,340,000"  # kept raw
    assert item.economic.cost == Decimal("0.03") and item.economic.payment.status == PaymentStatus.VERIFIED
    assert len(result.payments) == 1 and result.payments[0].evidence_id == item.evidence_id
    assert result.spent == Decimal("0.03")
    now = ctx.now() + timedelta(seconds=1)
    decision = evaluate(contract, result.evidence_set(contract.contract_id), now, permitted_sources=permitted_sources(registry))
    assert decision.state == DecisionState.ABSTAIN
    assert decision.evidence_assessments[0].status == EvidenceStatus.INVALID
    assert decision.evidence_cost_usd == Decimal("0.03")


@pytest.mark.parametrize(
    "response, code",
    [
        (lambda r: httpx.Response(200, content=b"not json"), ReasonCode.INVALID_RESPONSE),
        (lambda r: httpx.Response(200, json={"claim": "available_liquidity"}), ReasonCode.MISSING_FIELD),
        (lambda r: httpx.Response(200, json=[1, 2]), ReasonCode.INVALID_RESPONSE),
        (lambda r: httpx.Response(200, json={"claim": "available_liquidity", "value": 1, "asset": "OTHER"}), ReasonCode.INVALID_RESPONSE),
    ],
)
def test_paid_structural_failures_carry_cost_and_payment(h, response, code):
    settings = _paid_settings(h)
    contract = h.make_contract(h.req("available_liquidity", "r_liq"))
    ctx = h.make_ctx(contract, settings, http=h.mock_http(paid_endpoint(response)))
    registry = {"x402_paid": X402PaidEvidenceAdapter(settings)}
    result = h.run(acquire(contract, [PlanStep("r_liq", "available_liquidity", ["x402_paid"])], ctx, registry))
    assert result.items == []
    failure = result.failures[0]
    assert failure.code == code and failure.cost_spent == Decimal("0.03")
    assert [p.status for p in result.payments] == [PaymentStatus.VERIFIED]
    assert result.payments[0].evidence_id is None and result.payments[0].error
    assert result.spent == Decimal("0.03") == ctx.budget.spent


def test_budget_gate_through_acquisition_records_refused_quote(h):
    settings = _paid_settings(h)
    contract = h.make_contract(h.req("available_liquidity", "r_liq"))
    seen: list[httpx.Request] = []
    ctx = h.make_ctx(contract, settings, http=h.mock_http(paid_endpoint(lambda r: httpx.Response(200, json={}), amount="90000", record=seen)), budget=EvidenceBudget(Decimal("0.05")))
    registry = {"x402_paid": X402PaidEvidenceAdapter(settings)}
    result = h.run(acquire(contract, [PlanStep("r_liq", "available_liquidity", ["x402_paid"])], ctx, registry))
    assert result.failures[0].code == ReasonCode.BUDGET_EXCEEDED and result.failures[0].cost_spent == 0
    assert [p.status for p in result.payments] == [PaymentStatus.BUDGET_EXCEEDED]
    assert all(PAYMENT_SIGNATURE_HEADER not in r.headers for r in seen)
    assert ctx.extras.get("x402_payer") is None or ctx.extras["x402_payer"].sign_count == 0
    decision = evaluate(contract, result.evidence_set(contract.contract_id), ctx.now())
    assert decision.state == DecisionState.ABSTAIN and ReasonCode.EVIDENCE_BUDGET_EXHAUSTED in decision.reason_codes


def test_timeout_after_send_through_adapter_signs_once(h):
    """Acquisition-level: the paid send times out once; the adapter's client resends the same payload."""
    calls = {"paid": 0}
    headers_seen: list[str] = []

    def paid(request):
        calls["paid"] += 1
        headers_seen.append(request.headers[PAYMENT_SIGNATURE_HEADER])
        if calls["paid"] == 1:
            raise httpx.ReadTimeout("lost", request=request)
        return httpx.Response(200, json={"claim": "available_liquidity", "value": 700000, "unit": "USD", "observed_at": "2026-09-30T11:59:58Z"})

    settings = _paid_settings(h)
    contract = h.make_contract(h.req("available_liquidity", "r_liq"))
    ctx = h.make_ctx(contract, settings, http=h.mock_http(paid_endpoint(paid)))
    registry = {"x402_paid": X402PaidEvidenceAdapter(settings)}
    result = h.run(acquire(contract, [PlanStep("r_liq", "available_liquidity", ["x402_paid"])], ctx, registry))
    assert len(result.items) == 1 and result.items[0].claim.value == 700000
    assert ctx.extras["x402_payer"].sign_count == 1
    assert len(headers_seen) == 2 and headers_seen[0] == headers_seen[1]
    assert result.spent == Decimal("0.03") == ctx.budget.spent
    assert ctx.extras[SIGNED_STORE_KEY] == {}  # definitive success: payload forgotten


def test_reference_adapter_end_to_end_signature_verified(h):
    settings = h.make_settings()
    app = create_x402_reference_app(settings)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://x402-reference.local") as ref:
            ctx = h.make_ctx(settings=settings, reference_http=ref)
            raw = await X402ReferenceAdapter().fetch(h.req("asset_price"), ctx)
            return ctx, raw

    ctx, raw = h.run(go())
    assert raw.provider == "x402-reference" and raw.mode == SourceMode.SIMULATED
    assert raw.source_family == "simulated-market"  # value comes from the simulated market
    assert raw.value == "0.428" and raw.unit == "USD"
    assert raw.payment.status == PaymentStatus.SIMULATED and raw.payment.settled_amount is None
    assert raw.cost == Decimal("0.02") and ctx.budget.spent == Decimal("0.02")
    assert app.state.x402.verified_payments == 1
    assert ctx.extras["x402_payer"].sign_count == 1


def test_reference_adapter_requires_in_process_client(h):
    ctx = h.make_ctx()
    with pytest.raises(AcquisitionError) as e:
        h.run(X402ReferenceAdapter().fetch(h.req("asset_price"), ctx))
    assert e.value.code == ReasonCode.SOURCE_NOT_CONFIGURED
    assert not X402ReferenceAdapter().is_configured(h.make_settings(x402_reference_enabled=False))


def test_reference_adapter_wrong_recipient_rejected_before_signing(h):
    """Allowlist excludes the reference server's payTo: nothing is signed, nothing is paid."""
    settings = h.make_settings(x402_pay_to_allowlist="0x000000000000000000000000000000000000bEEF")
    app = create_x402_reference_app(settings)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://x402-reference.local") as ref:
            ctx = h.make_ctx(settings=settings, reference_http=ref)
            with pytest.raises(AcquisitionError) as e:
                await X402ReferenceAdapter().fetch(h.req("asset_price"), ctx)
            return ctx, e.value

    ctx, err = h.run(go())
    assert err.code == ReasonCode.PAYMENT_FAILED and err.payment.status == PaymentStatus.REJECTED
    assert ctx.extras["x402_payer"].sign_count == 0 and ctx.budget.spent == 0
    assert app.state.x402.verified_payments == 0


def test_nansen_x402_pay_per_call_mode(h):
    rows = {"data": [{"token_address": h.TOKEN, "chain": "monad", "net_flow_24h_usd": "1500000"}]}
    seen: list[httpx.Request] = []
    settings = h.make_settings(asset_token_address=h.TOKEN, nansen_x402=True)
    ctx = h.make_ctx(settings=settings, http=h.mock_http(paid_endpoint(lambda r: h.json_response(200, rows), amount="10000", record=seen)))
    raw = h.run(NansenSmartMoneyAdapter(settings).fetch(h.req("smart_money_inflow_24h"), ctx))
    assert raw.value == "1500000" and raw.cost == Decimal("0.01")
    assert raw.payment.status == PaymentStatus.VERIFIED
    assert "apikey" not in seen[-1].headers
    assert raw.request_descriptor["payment"] == "x402"


def test_acquisition_timeout_after_payment_sent_is_counted_as_spent(h):
    """The paid send hangs past the per-source timeout: the attempt is cancelled, but the signed
    authorization already left the process, so its cost and a TIMEOUT payment are recorded."""
    import asyncio

    async def hang(request):
        if PAYMENT_SIGNATURE_HEADER not in request.headers:
            required = _required()
            return httpx.Response(402, json=required, headers={PAYMENT_REQUIRED_HEADER: b64_json_encode(required)})
        await asyncio.sleep(30)
        return httpx.Response(200, json={})

    settings = _paid_settings(h, http_timeout_seconds=0.01)
    contract = h.make_contract(h.req("available_liquidity", "r_liq"))
    ctx = h.make_ctx(contract, settings, http=httpx.AsyncClient(transport=httpx.MockTransport(hang)))
    registry = {"x402_paid": X402PaidEvidenceAdapter(settings)}
    result = h.run(acquire(contract, [PlanStep("r_liq", "available_liquidity", ["x402_paid"])], ctx, registry))
    failure = result.failures[0]
    assert failure.code == ReasonCode.SOURCE_UNAVAILABLE and failure.cost_spent == Decimal("0.03")
    assert [p.status for p in result.payments] == [PaymentStatus.TIMEOUT]
    assert result.spent == Decimal("0.03") == ctx.budget.spent
    assert ctx.extras["x402_payer"].sign_count == 1


def test_concurrent_paid_requirements_never_overspend(h):
    """Five concurrent paid acquisitions at 0.02 against a 0.05 budget: exactly two are signed."""
    settings = h.make_settings()
    app = create_x402_reference_app(settings)
    reqs = [h.req("available_liquidity", f"r{i}") for i in range(5)]
    contract = h.make_contract(*reqs, budget="0.05")
    steps = [PlanStep(r.id, r.claim, ["x402_reference"]) for r in reqs]

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://x402-reference.local") as ref:
            ctx = h.make_ctx(contract, settings, reference_http=ref, budget=EvidenceBudget(Decimal("0.05"), Decimal("0.10")))
            return ctx, await acquire(contract, steps, ctx, {"x402_reference": X402ReferenceAdapter()})

    ctx, result = h.run(go())
    assert len(result.items) == 2
    assert sorted(f.code for f in result.failures) == [ReasonCode.BUDGET_EXCEEDED] * 3
    assert ctx.extras["x402_payer"].sign_count == 2 == app.state.x402.verified_payments
    assert result.spent == Decimal("0.04") == ctx.budget.spent <= Decimal("0.05")
    assert sorted(p.status.value for p in result.payments) == ["BUDGET_EXCEEDED"] * 3 + ["SIMULATED"] * 2
