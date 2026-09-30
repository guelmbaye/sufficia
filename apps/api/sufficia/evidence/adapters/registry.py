"""Source registry: stable profile names -> adapter instances.

The names used as keys are the ones referenced by evidence profiles in ``config.DEFAULT_SOURCES``
(``settings.sources_for_profile()``); each adapter's ``name`` is the provider identity recorded in
provenance (e.g. key "x402_reference" -> provider "x402-reference").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import SourceAdapter
from .chainlink import ChainlinkPriceAdapter
from .dexscreener import DexScreenerAdapter
from .nansen import NansenSmartMoneyAdapter
from .recorded import RecordedFixtureAdapter
from .scenario import ScenarioAdapter
from .uniswap_v2_pool import UniswapV2PoolLiquidityAdapter
from .x402_paid import X402PaidEvidenceAdapter, X402ReferenceAdapter

if TYPE_CHECKING:  # pragma: no cover
    from ...config import Settings

REGISTRY_NAMES = ("scenario", "x402_reference", "x402_paid", "nansen", "dexscreener", "uniswap_v2_pool", "chainlink", "recorded")


def build_registry(settings: "Settings") -> dict[str, SourceAdapter]:
    """All known adapters, configured or not (the planner skips unconfigured ones with a reason)."""
    return {
        "scenario": ScenarioAdapter(),
        "x402_reference": X402ReferenceAdapter(),
        "x402_paid": X402PaidEvidenceAdapter(settings),
        "nansen": NansenSmartMoneyAdapter(settings),
        "dexscreener": DexScreenerAdapter(),
        "uniswap_v2_pool": UniswapV2PoolLiquidityAdapter(),
        "chainlink": ChainlinkPriceAdapter(),
        "recorded": RecordedFixtureAdapter(),
    }


def permitted_sources(registry: dict[str, SourceAdapter], settings: "Settings | None" = None) -> dict[str, set[str]]:
    """provider name -> claims it may provide; feeds ``engine.evaluate(permitted_sources=...)``.

    The RECORDED adapter re-serves fixtures under their *original* provider name, so when
    ``settings`` is given the providers found in the fixture directory are added for the claims
    they were recorded for.
    """
    out: dict[str, set[str]] = {}
    for adapter in registry.values():
        out.setdefault(adapter.name, set()).update(adapter.claims)
        if settings is not None and isinstance(adapter, RecordedFixtureAdapter):
            for provider, claims in adapter.fixture_providers(settings).items():
                out.setdefault(provider, set()).update(claims)
    return out
