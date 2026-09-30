"""SIMULATED source backed by the in-memory simulated market (demo profile).

Every observation produced here is labelled ``SIMULATED`` and belongs to the ``simulated-market``
independence family: it is *not* market data. The Demo Lab changes MARKET values to produce
reproducible PASS/FAIL conditions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...canonical.canonicalize import canonical_bytes
from ...domain.claims import get_claim
from ...domain.enums import SourceMode, SourceType
from ...domain.models import Requirement
from ...domain.reasons import ReasonCode
from ..base import AcquisitionContext, AcquisitionError, RawEvidence, SourceAdapter
from ..simulated_market import DEFAULT_VALUES, MARKET, SimulatedMarket

if TYPE_CHECKING:  # pragma: no cover
    from ...config import Settings

SIMULATED_FAMILY = "simulated-market"


class ScenarioAdapter(SourceAdapter):
    name = "simulated-market"
    source_type = SourceType.API
    mode = SourceMode.SIMULATED
    claims = frozenset(DEFAULT_VALUES)
    paid = False

    def __init__(self, market: SimulatedMarket | None = None):
        self.market = market or MARKET

    def is_configured(self, settings: "Settings") -> bool:
        return True

    async def fetch(self, requirement: Requirement, ctx: AcquisitionContext) -> RawEvidence:
        claim = requirement.claim
        asset = ctx.asset
        observation = self.market.get(asset, claim)
        if observation is None:
            raise AcquisitionError(ReasonCode.MISSING_FIELD, f"simulated market has no '{claim}' for {asset}")
        spec = get_claim(claim)
        unit = spec.unit if spec else None
        observed_at = ctx.now()
        body = canonical_bytes(
            {
                "asset": observation.asset,
                "claim": observation.claim,
                "value": observation.value,
                "unit": unit,
                "version": observation.version,
                "observed_at": observed_at,
                "mode": SourceMode.SIMULATED.value,
            }
        )
        return RawEvidence(
            provider=self.name,
            source_type=self.source_type,
            mode=self.mode,
            claim=claim,
            value=observation.value,
            unit=unit,
            observed_at=observed_at,
            received_at=ctx.now(),
            request_descriptor={"method": "GET", "url": f"sim://market/{asset}/{claim}"},
            response_body=body,
            endpoint_id="sim://market",
            upstream_source=SIMULATED_FAMILY,
            source_family=SIMULATED_FAMILY,
        )
