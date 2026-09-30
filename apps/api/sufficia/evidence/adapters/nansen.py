"""Nansen smart-money netflow (LIVE, API) -> ``smart_money_inflow_24h`` (USD).

Endpoint: ``POST {nansen_base_url}/api/v1/smart-money/netflow`` with header ``apikey: <key>``::

    {"chains": [nansen_chain], "filters": {"token_address": <asset_token_address>},
     "pagination": {"page": 1, "per_page": 10}}

Response: ``{"data": [{"token_address", "token_symbol", "chain", "net_flow_24h_usd", ...}, ...]}``.
The row whose ``token_address`` matches (case-insensitive) provides ``net_flow_24h_usd``.

Timestamp: Nansen returns no per-row observation time for this aggregate, so ``observed_at`` is the
time the response was received. That is the honest bound we can state; it is recorded as such.

Authentication: API key (never placed in the request descriptor, so it never reaches a hash or a
receipt). When no key is configured, ``settings.x402_enabled`` and ``settings.nansen_x402`` are set,
the adapter pays per call with x402 instead (Nansen accepts ``PAYMENT-SIGNATURE``). Nansen's x402
network/asset may differ from the Monad evidence-payment defaults; they can be set with the optional
settings ``nansen_x402_network`` / ``nansen_x402_asset`` (default: ``x402_network`` / ``x402_asset``).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from ...domain.enums import SourceMode, SourceType
from ...domain.models import EvidencePayment, Requirement
from ...domain.reasons import ReasonCode
from ..base import AcquisitionContext, AcquisitionError, RawEvidence, SourceAdapter
from ._common import http_request, parse_json_bytes, raise_for_status
from ._paid import x402_client_for

if TYPE_CHECKING:  # pragma: no cover
    from ...config import Settings

NETFLOW_PATH = "/api/v1/smart-money/netflow"


def _x402_mode(settings: "Settings") -> bool:
    return (
        not settings.nansen_api_key
        and bool(settings.x402_enabled)
        and bool(getattr(settings, "nansen_x402", False))
    )


class NansenSmartMoneyAdapter(SourceAdapter):
    name = "nansen"
    source_type = SourceType.API
    mode = SourceMode.LIVE
    claims = frozenset({"smart_money_inflow_24h"})
    family = "nansen"
    paid = False

    def __init__(self, settings: "Settings | None" = None):
        # Pay-per-call instances are flagged paid so the planner / UI can show the cost path.
        self.paid = bool(settings is not None and _x402_mode(settings))

    def is_configured(self, settings: "Settings") -> bool:
        if not settings.asset_token_address:
            return False
        return bool(settings.nansen_api_key) or _x402_mode(settings)

    async def fetch(self, requirement: Requirement, ctx: AcquisitionContext) -> RawEvidence:
        settings = ctx.settings
        token = settings.asset_token_address
        if not token or not self.is_configured(settings):
            raise AcquisitionError(ReasonCode.SOURCE_NOT_CONFIGURED, "nansen: api key / asset_token_address not configured")
        url = settings.nansen_base_url.rstrip("/") + NETFLOW_PATH
        body = {
            "chains": [settings.nansen_chain],
            "filters": {"token_address": token},
            "pagination": {"page": 1, "per_page": 10},
        }
        descriptor = {"method": "POST", "url": url, "body": body}
        cost = Decimal(0)
        payment: Optional[EvidencePayment] = None
        extra: dict[str, Any] = {}

        if settings.nansen_api_key:
            response = await http_request(
                ctx.http,
                "POST",
                url,
                provider=self.name,
                timeout=settings.http_timeout_seconds,
                json=body,
                headers={"apikey": settings.nansen_api_key, "accept": "application/json"},
            )
        else:
            descriptor["payment"] = "x402"
            client = x402_client_for(
                ctx,
                ctx.http,
                network=getattr(settings, "nansen_x402_network", None),
                asset=getattr(settings, "nansen_x402_asset", None),
            )
            result = await client.request(
                "POST",
                url,
                budget=ctx.budget,
                idempotency_key=ctx.acquisition_id(requirement.id, self.name),
                json_body=body,
                headers={"accept": "application/json"},
            )
            response, cost, payment = result.response, result.cost, result.payment
            extra = {"cost_spent": cost, "payment": payment}
        received_at = ctx.now()

        raise_for_status(response, self.name, **extra)
        data = parse_json_bytes(response.content, self.name, **extra)
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise AcquisitionError(ReasonCode.INVALID_RESPONSE, "nansen response has no 'data' list", **extra)
        row = next(
            (r for r in rows if isinstance(r, dict) and str(r.get("token_address", "")).lower() == token.lower()),
            None,
        )
        if row is None:
            raise AcquisitionError(ReasonCode.MISSING_FIELD, f"nansen returned no netflow row for {token}", **extra)
        if "net_flow_24h_usd" not in row:
            raise AcquisitionError(ReasonCode.MISSING_FIELD, "nansen row has no 'net_flow_24h_usd'", **extra)

        return RawEvidence(
            provider=self.name,
            source_type=self.source_type,
            mode=self.mode,
            claim="smart_money_inflow_24h",
            value=row["net_flow_24h_usd"],  # raw; the normalizer parses strictly
            unit="USD",
            observed_at=received_at,  # Nansen gives no per-row timestamp (see module docstring)
            received_at=received_at,
            request_descriptor=descriptor,
            response_body=response.content,
            endpoint_id=f"nansen:{NETFLOW_PATH}",
            upstream_source="nansen:smart-money-netflow",
            source_family=self.family,
            network=str(row.get("chain") or settings.nansen_chain),
            cost=cost,
            payment=payment,
        )
