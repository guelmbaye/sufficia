"""Evidence source adapters (untrusted input boundaries; they never return PASS/FAIL)."""

from .chainlink import ChainlinkPriceAdapter
from .dexscreener import DexScreenerAdapter
from .nansen import NansenSmartMoneyAdapter
from .recorded import RecordedFixtureAdapter
from .registry import REGISTRY_NAMES, build_registry, permitted_sources
from .scenario import ScenarioAdapter
from .uniswap_v2_pool import UniswapV2PoolLiquidityAdapter
from .x402_paid import X402PaidEvidenceAdapter, X402ReferenceAdapter

__all__ = [
    "REGISTRY_NAMES",
    "ChainlinkPriceAdapter",
    "DexScreenerAdapter",
    "NansenSmartMoneyAdapter",
    "RecordedFixtureAdapter",
    "ScenarioAdapter",
    "UniswapV2PoolLiquidityAdapter",
    "X402PaidEvidenceAdapter",
    "X402ReferenceAdapter",
    "build_registry",
    "permitted_sources",
]
