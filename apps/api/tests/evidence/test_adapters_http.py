"""SIMULATED scenario, Nansen and DexScreener adapters against httpx.MockTransport (no network)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from sufficia.canonical.canonicalize import canonical_bytes
from sufficia.domain.enums import SourceMode, SourceType
from sufficia.domain.reasons import ReasonCode
from sufficia.evidence.adapters import DexScreenerAdapter, NansenSmartMoneyAdapter, ScenarioAdapter
from sufficia.evidence.base import AcquisitionError
from sufficia.evidence.simulated_market import MARKET

FIXED_NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)


# ---- scenario (SIMULATED) ----------------------------------------------------------------------


def test_scenario_reads_simulated_market(h):
    MARKET.set("TOKEN-X", "available_liquidity", Decimal("612000"))
    ctx = h.make_ctx(now=lambda: FIXED_NOW)
    raw = h.run(ScenarioAdapter().fetch(h.req("available_liquidity"), ctx))
    assert raw.provider == "simulated-market"
    assert raw.mode == SourceMode.SIMULATED and raw.source_type == SourceType.API
    assert raw.source_family == "simulated-market"
    assert raw.value == Decimal("612000") and raw.unit == "USD"
    assert raw.observed_at == FIXED_NOW
    assert raw.request_descriptor == {"method": "GET", "url": "sim://market/TOKEN-X/available_liquidity"}
    body = json.loads(raw.response_body)
    assert body["value"] == 612000 and body["mode"] == "SIMULATED"
    assert raw.response_body == canonical_bytes(json.loads(raw.response_body, parse_float=Decimal))


def test_scenario_unknown_claim_is_missing_field(h):
    with pytest.raises(AcquisitionError) as e:
        h.run(ScenarioAdapter().fetch(h.req("venue_id", values=["x"], operator="IN"), h.make_ctx()))
    assert e.value.code == ReasonCode.MISSING_FIELD


# ---- Nansen --------------------------------------------------------------------------------------


def _nansen_settings(h, **kw):
    base = dict(nansen_api_key="secret-key", asset_token_address=h.TOKEN)
    base.update(kw)
    return h.make_settings(**base)


def _nansen_rows(h, value="1340000.5"):
    return {
        "data": [
            {"token_address": "0x9999999999999999999999999999999999999999", "token_symbol": "OTHER", "chain": "monad", "net_flow_24h_usd": 5},
            {"token_address": h.TOKEN.upper().replace("0X", "0x"), "token_symbol": "TKX", "chain": "monad", "net_flow_24h_usd": value, "trader_count": 12},
        ]
    }


def test_nansen_happy_path_request_shape_and_secret_hygiene(h):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return h.json_response(200, _nansen_rows(h, 1340000.5))

    ctx = h.make_ctx(settings=_nansen_settings(h), http=h.mock_http(handler), now=lambda: FIXED_NOW)
    adapter = NansenSmartMoneyAdapter(ctx.settings)
    raw = h.run(adapter.fetch(h.req("smart_money_inflow_24h"), ctx))

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "https://nansen.test/api/v1/smart-money/netflow"
    assert request.headers["apikey"] == "secret-key"
    assert json.loads(request.content) == {
        "chains": ["monad"],
        "filters": {"token_address": h.TOKEN},
        "pagination": {"page": 1, "per_page": 10},
    }
    assert raw.value == Decimal("1340000.5")  # JSON number parsed as Decimal, never float
    assert raw.unit == "USD" and raw.mode == SourceMode.LIVE and raw.source_family == "nansen"
    assert raw.observed_at == FIXED_NOW  # no per-row timestamp at Nansen: response time
    assert "secret-key" not in json.dumps(raw.request_descriptor)
    assert raw.response_body == json.dumps(_nansen_rows(h, 1340000.5)).encode()  # exact bytes received


@pytest.mark.parametrize(
    "status, code",
    [(401, ReasonCode.SOURCE_NOT_CONFIGURED), (403, ReasonCode.SOURCE_NOT_CONFIGURED), (429, ReasonCode.RATE_LIMITED), (500, ReasonCode.SOURCE_UNAVAILABLE), (503, ReasonCode.SOURCE_UNAVAILABLE), (400, ReasonCode.INVALID_RESPONSE)],
)
def test_nansen_http_error_mapping(h, status, code):
    ctx = h.make_ctx(settings=_nansen_settings(h), http=h.mock_http(lambda r: h.json_response(status, {"error": "x"})))
    with pytest.raises(AcquisitionError) as e:
        h.run(NansenSmartMoneyAdapter().fetch(h.req("smart_money_inflow_24h"), ctx))
    assert e.value.code == code


def test_nansen_timeout_is_source_unavailable(h):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    ctx = h.make_ctx(settings=_nansen_settings(h), http=h.mock_http(handler))
    with pytest.raises(AcquisitionError) as e:
        h.run(NansenSmartMoneyAdapter().fetch(h.req("smart_money_inflow_24h"), ctx))
    assert e.value.code == ReasonCode.SOURCE_UNAVAILABLE


def test_nansen_row_absent_is_missing_field(h):
    body = {"data": [{"token_address": "0x9999999999999999999999999999999999999999", "net_flow_24h_usd": 5}]}
    ctx = h.make_ctx(settings=_nansen_settings(h), http=h.mock_http(lambda r: h.json_response(200, body)))
    with pytest.raises(AcquisitionError) as e:
        h.run(NansenSmartMoneyAdapter().fetch(h.req("smart_money_inflow_24h"), ctx))
    assert e.value.code == ReasonCode.MISSING_FIELD


def test_nansen_bad_json_is_invalid_response(h):
    ctx = h.make_ctx(settings=_nansen_settings(h), http=h.mock_http(lambda r: httpx.Response(200, content=b"<html>oops")))
    with pytest.raises(AcquisitionError) as e:
        h.run(NansenSmartMoneyAdapter().fetch(h.req("smart_money_inflow_24h"), ctx))
    assert e.value.code == ReasonCode.INVALID_RESPONSE


def test_nansen_malformed_value_passes_through_raw(h):
    ctx = h.make_ctx(settings=_nansen_settings(h), http=h.mock_http(lambda r: h.json_response(200, _nansen_rows(h, "1,340,000"))))
    raw = h.run(NansenSmartMoneyAdapter().fetch(h.req("smart_money_inflow_24h"), ctx))
    assert raw.value == "1,340,000"


def test_nansen_configuration(h):
    adapter = NansenSmartMoneyAdapter()
    assert not adapter.is_configured(h.make_settings())
    assert not adapter.is_configured(h.make_settings(nansen_api_key="k"))  # no token address
    assert adapter.is_configured(_nansen_settings(h))
    assert adapter.is_configured(h.make_settings(asset_token_address=h.TOKEN, nansen_x402=True))
    assert not adapter.is_configured(h.make_settings(asset_token_address=h.TOKEN, nansen_x402=True, x402_enabled=False))
    assert NansenSmartMoneyAdapter(h.make_settings(asset_token_address=h.TOKEN, nansen_x402=True)).paid is True
    assert NansenSmartMoneyAdapter(_nansen_settings(h)).paid is False


# ---- DexScreener ---------------------------------------------------------------------------------


def _pairs():
    return [
        {"chainId": "monad", "dexId": "uniswap", "pairAddress": "0xAAA0000000000000000000000000000000000001", "priceUsd": "0.4270", "liquidity": {"usd": 250000.5, "base": 1, "quote": 2}, "pairCreatedAt": 1717000000000},
        {"chainId": "monad", "dexId": "kuru", "pairAddress": "0xBBB0000000000000000000000000000000000002", "priceUsd": "0.4281", "liquidity": {"usd": 812345.12}, "pairCreatedAt": 1717000000000},
        {"chainId": "monad", "dexId": "junk", "pairAddress": "0xCCC0000000000000000000000000000000000003", "priceUsd": "0.1", "liquidity": {"usd": "not-a-number"}},
    ]


def _dex_ctx(h, handler, **kw):
    return h.make_ctx(settings=h.make_settings(asset_token_address=h.TOKEN, **kw), http=h.mock_http(handler), now=lambda: FIXED_NOW)


def test_dexscreener_max_liquidity_pair(h):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return h.json_response(200, _pairs())

    ctx = _dex_ctx(h, handler)
    liq = h.run(DexScreenerAdapter().fetch(h.req("available_liquidity"), ctx))
    price = h.run(DexScreenerAdapter().fetch(h.req("asset_price"), ctx))
    assert seen[0] == f"https://dexscreener.test/tokens/v1/monad/{h.TOKEN}"
    assert liq.value == Decimal("812345.12") and liq.unit == "USD"
    assert price.value == "0.4281"  # served as a string; the normalizer parses it
    assert liq.request_descriptor["selected_pair"] == "0xBBB0000000000000000000000000000000000002"
    assert liq.source_family == "dexscreener" and liq.mode == SourceMode.LIVE
    assert liq.observed_at == FIXED_NOW


def test_dexscreener_configured_pair_wins_and_missing_pair_is_not_substituted(h):
    ctx = _dex_ctx(h, lambda r: h.json_response(200, _pairs()), dexscreener_pair_address="0xaaa0000000000000000000000000000000000001")
    raw = h.run(DexScreenerAdapter().fetch(h.req("available_liquidity"), ctx))
    assert raw.value == Decimal("250000.5")
    ctx = _dex_ctx(h, lambda r: h.json_response(200, _pairs()), dexscreener_pair_address="0xDDD0000000000000000000000000000000000004")
    with pytest.raises(AcquisitionError) as e:
        h.run(DexScreenerAdapter().fetch(h.req("available_liquidity"), ctx))
    assert e.value.code == ReasonCode.MISSING_FIELD


@pytest.mark.parametrize(
    "response, code",
    [
        (lambda h: h.json_response(429, {}), ReasonCode.RATE_LIMITED),
        (lambda h: h.json_response(502, {}), ReasonCode.SOURCE_UNAVAILABLE),
        (lambda h: httpx.Response(200, content=b"{not json"), ReasonCode.INVALID_RESPONSE),
        (lambda h: h.json_response(200, {"schemaVersion": "1"}), ReasonCode.INVALID_RESPONSE),
        (lambda h: h.json_response(200, []), ReasonCode.MISSING_FIELD),
        (lambda h: h.json_response(200, [{"pairAddress": "0x1", "liquidity": {"usd": 5}}]), None),
    ],
)
def test_dexscreener_errors(h, response, code):
    ctx = _dex_ctx(h, lambda r: response(h))
    if code is None:  # pair without priceUsd -> MISSING_FIELD only for asset_price
        with pytest.raises(AcquisitionError) as e:
            h.run(DexScreenerAdapter().fetch(h.req("asset_price"), ctx))
        assert e.value.code == ReasonCode.MISSING_FIELD
        return
    with pytest.raises(AcquisitionError) as e:
        h.run(DexScreenerAdapter().fetch(h.req("available_liquidity"), ctx))
    assert e.value.code == code


def test_dexscreener_not_configured_without_token(h):
    assert not DexScreenerAdapter().is_configured(h.make_settings())
    with pytest.raises(AcquisitionError) as e:
        h.run(DexScreenerAdapter().fetch(h.req("asset_price"), h.make_ctx()))
    assert e.value.code == ReasonCode.SOURCE_NOT_CONFIGURED
