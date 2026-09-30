"""DexScreener token pairs (LIVE, API) -> ``available_liquidity`` and ``asset_price`` (USD).

Endpoint: ``GET {dexscreener_base_url}/tokens/v1/{dexscreener_chain_id}/{asset_token_address}``,
which returns a JSON array of pairs::

    [{"chainId", "dexId", "pairAddress", "baseToken": {...}, "quoteToken": {...},
      "priceUsd": "0.4281", "liquidity": {"usd": 812345.12, ...}, "pairCreatedAt": 1717000000000, ...}]

Pair selection: ``dexscreener_pair_address`` when configured (a missing configured pair is a
MISSING_FIELD failure, never a silent substitution), otherwise the pair with the highest
``liquidity.usd``.

Values are passed through as served: ``priceUsd`` is a *string* (the normalizer parses it
strictly), ``liquidity.usd`` a JSON number (parsed as Decimal, never float).

Timestamp: DexScreener pairs carry no observation time (``pairCreatedAt`` is the pool creation time),
so ``observed_at`` is the time the response was received.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from ...domain.enums import SourceMode, SourceType
from ...domain.models import Requirement
from ...domain.reasons import ReasonCode
from ..base import AcquisitionContext, AcquisitionError, RawEvidence, SourceAdapter
from ._common import as_decimal_or_none, http_request, parse_json_bytes, raise_for_status

if TYPE_CHECKING:  # pragma: no cover
    from ...config import Settings


class DexScreenerAdapter(SourceAdapter):
    name = "dexscreener"
    source_type = SourceType.API
    mode = SourceMode.LIVE
    claims = frozenset({"available_liquidity", "asset_price"})
    family = "dexscreener"

    def is_configured(self, settings: "Settings") -> bool:
        return bool(settings.asset_token_address and settings.dexscreener_base_url and settings.dexscreener_chain_id)

    @staticmethod
    def select_pair(pairs: list[Any], pair_address: Optional[str]) -> Optional[dict[str, Any]]:
        candidates = [p for p in pairs if isinstance(p, dict)]
        if pair_address:
            return next((p for p in candidates if str(p.get("pairAddress", "")).lower() == pair_address.lower()), None)
        best: Optional[dict[str, Any]] = None
        best_liq = None
        for p in candidates:
            liq = p.get("liquidity")
            liq_usd = as_decimal_or_none(liq.get("usd")) if isinstance(liq, dict) else None
            if liq_usd is None:
                continue
            if best_liq is None or liq_usd > best_liq:
                best, best_liq = p, liq_usd
        return best

    async def fetch(self, requirement: Requirement, ctx: AcquisitionContext) -> RawEvidence:
        settings = ctx.settings
        claim = requirement.claim
        if claim not in self.claims:
            raise AcquisitionError(ReasonCode.SOURCE_NOT_CONFIGURED, f"dexscreener does not provide '{claim}'")
        if not self.is_configured(settings):
            raise AcquisitionError(ReasonCode.SOURCE_NOT_CONFIGURED, "dexscreener: asset_token_address not configured")
        token = settings.asset_token_address
        url = f"{settings.dexscreener_base_url.rstrip('/')}/tokens/v1/{settings.dexscreener_chain_id}/{token}"
        response = await http_request(
            ctx.http, "GET", url, provider=self.name, timeout=settings.http_timeout_seconds, headers={"accept": "application/json"}
        )
        received_at = ctx.now()
        raise_for_status(response, self.name)
        data = parse_json_bytes(response.content, self.name)
        if isinstance(data, dict) and isinstance(data.get("pairs"), list):  # legacy /latest/dex shape
            data = data["pairs"]
        if not isinstance(data, list):
            raise AcquisitionError(ReasonCode.INVALID_RESPONSE, "dexscreener response is not a list of pairs")
        pair = self.select_pair(data, settings.dexscreener_pair_address)
        if pair is None:
            which = settings.dexscreener_pair_address or "any pair with liquidity"
            raise AcquisitionError(ReasonCode.MISSING_FIELD, f"dexscreener returned no pair for {token} ({which})")

        if claim == "available_liquidity":
            liq = pair.get("liquidity")
            if not isinstance(liq, dict) or "usd" not in liq:
                raise AcquisitionError(ReasonCode.MISSING_FIELD, "dexscreener pair has no liquidity.usd")
            value = liq["usd"]
        else:
            if "priceUsd" not in pair:
                raise AcquisitionError(ReasonCode.MISSING_FIELD, "dexscreener pair has no priceUsd")
            value = pair["priceUsd"]

        pair_address = pair.get("pairAddress")
        return RawEvidence(
            provider=self.name,
            source_type=self.source_type,
            mode=self.mode,
            claim=claim,
            value=value,
            unit="USD",
            observed_at=received_at,  # no per-pair observation time (see module docstring)
            received_at=received_at,
            request_descriptor={"method": "GET", "url": url, "selected_pair": pair_address},
            response_body=response.content,
            endpoint_id=f"dexscreener:tokens/v1/{settings.dexscreener_chain_id}",
            upstream_source=f"dexscreener:{pair.get('dexId') or 'dex'}:{pair_address}",
            source_family=self.family,
            network=str(pair.get("chainId") or settings.dexscreener_chain_id),
        )
