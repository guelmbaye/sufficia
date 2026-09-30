from __future__ import annotations

import json

from sufficia.evidence.adapters import REGISTRY_NAMES, build_registry, permitted_sources
from sufficia.evidence.planner import SKIP_CLAIM, SKIP_NO_PROFILE_ENTRY, SKIP_NOT_CONFIGURED, SKIP_UNKNOWN, plan


def test_registry_has_stable_profile_names_and_provider_identities(h):
    registry = build_registry(h.make_settings())
    assert tuple(registry) == REGISTRY_NAMES
    assert {k: a.name for k, a in registry.items()} == {
        "scenario": "simulated-market",
        "x402_reference": "x402-reference",
        "x402_paid": "x402-paid-evidence",
        "nansen": "nansen",
        "dexscreener": "dexscreener",
        "uniswap_v2_pool": "uniswap-v2-pool",
        "chainlink": "chainlink",
        "recorded": "recorded",
    }
    assert registry["x402_reference"].paid and registry["x402_paid"].paid


def test_permitted_sources_maps_providers_to_claims(h):
    settings = h.make_settings()
    registry = build_registry(settings)
    permitted = permitted_sources(registry)
    assert permitted["nansen"] == {"smart_money_inflow_24h"}
    assert permitted["dexscreener"] == {"available_liquidity", "asset_price"}
    assert permitted["chainlink"] == {"asset_price"}
    assert "asset_price" in permitted["simulated-market"]
    # fixtures are re-served under their original provider name
    with_fixtures = permitted_sources(registry, settings)
    assert "available_liquidity" in with_fixtures["simulated-market"]


def test_demo_profile_plan(h):
    settings = h.make_settings(evidence_profile="demo")
    steps = plan(h.make_contract(), settings, build_registry(settings))
    by_claim = {s.claim: s for s in steps}
    assert by_claim["available_liquidity"].adapters == ["x402_reference", "scenario"]
    assert by_claim["smart_money_inflow_24h"].adapters == ["scenario"]
    assert [s.requirement_id for s in steps] == ["r_flow", "r_liq", "r_price"]


def test_live_profile_skips_unconfigured_sources_with_reasons(h):
    settings = h.make_settings(evidence_profile="live", asset_token_address=h.TOKEN)  # only DexScreener usable
    steps = {s.claim: s for s in plan(h.make_contract(), settings, build_registry(settings))}
    liq = steps["available_liquidity"]
    assert liq.adapters == ["dexscreener"]
    assert ("x402_paid", SKIP_NOT_CONFIGURED) in liq.skipped and ("uniswap_v2_pool", SKIP_NOT_CONFIGURED) in liq.skipped
    assert steps["asset_price"].adapters == ["dexscreener"]  # chainlink skipped (no feed)
    flow = steps["smart_money_inflow_24h"]
    assert flow.adapters == [] and flow.skipped == [("nansen", SKIP_NOT_CONFIGURED)]


def test_fallback_order_follows_profile_when_configured(h):
    settings = h.make_settings(
        evidence_profile="live",
        asset_token_address=h.TOKEN,
        chainlink_feed_address=h.FEED,
        pool_address=h.POOL,
        x402_evidence_url="https://paid.test/evidence",
    )
    steps = {s.claim: s for s in plan(h.make_contract(), settings, build_registry(settings))}
    assert steps["available_liquidity"].adapters == ["x402_paid", "dexscreener", "uniswap_v2_pool"]
    assert steps["asset_price"].adapters == ["chainlink", "dexscreener"]


def test_unknown_source_wrong_claim_and_missing_profile_entry(h):
    sources = {"available_liquidity": ["nope", "chainlink", "scenario", "scenario"], "asset_price": ["scenario"]}
    settings = h.make_settings(evidence_sources_json=json.dumps(sources))
    steps = {s.claim: s for s in plan(h.make_contract(), settings, build_registry(settings))}
    liq = steps["available_liquidity"]
    assert liq.adapters == ["scenario"]  # de-duplicated
    assert ("nope", SKIP_UNKNOWN) in liq.skipped and ("chainlink", SKIP_CLAIM) in liq.skipped
    flow = steps["smart_money_inflow_24h"]
    assert flow.adapters == [] and flow.skipped == [("*", SKIP_NO_PROFILE_ENTRY)]
    assert not flow.has_sources
