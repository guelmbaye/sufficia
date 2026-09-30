"""Paid evidence over x402.

* :class:`X402PaidEvidenceAdapter` ("x402-paid-evidence", LIVE): a real x402-protected endpoint at
  ``settings.x402_evidence_url`` (payments on ``settings.x402_network``).
* :class:`X402ReferenceAdapter` ("x402-reference", SIMULATED): pays the *local* x402 reference
  resource server (``sufficia.devtools.x402_reference``) through ``ctx.reference_http`` (in-process
  ASGI transport). The EIP-3009 authorization is genuinely signed and genuinely verified by the
  server, but nothing is settled on-chain and the evidence value comes from the simulated market,
  so both the observation and the payment are labelled SIMULATED.

Both endpoints serve the SUFFICIA paid-evidence JSON format::

    {"claim", "value", "unit", "observed_at", "asset", "provider", "upstream_source"?}

Order of operations: quote (402) -> validate network/asset/recipient/cap -> ``ctx.budget.try_reserve``
-> sign once -> resend -> parse. Payment success never makes evidence valid: the returned
RawEvidence still goes through the normalizer and the Decision Engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional
from urllib.parse import urlsplit

from ...domain.enums import SourceMode, SourceType
from ...domain.models import Requirement
from ...domain.reasons import ReasonCode
from ..base import AcquisitionContext, AcquisitionError, RawEvidence, SourceAdapter
from ..simulated_market import DEFAULT_VALUES
from ._common import raise_for_status
from ._paid import evidence_json_to_raw, x402_client_for
from .scenario import SIMULATED_FAMILY

if TYPE_CHECKING:  # pragma: no cover
    from ...config import Settings

PAID_CLAIMS = frozenset({"available_liquidity", "asset_price", "smart_money_inflow_24h"})
REFERENCE_BASE_URL = "http://x402-reference.local"


def _host(url: str) -> str:
    return urlsplit(url).netloc or "unknown"


class X402PaidEvidenceAdapter(SourceAdapter):
    name = "x402-paid-evidence"
    source_type = SourceType.API
    mode = SourceMode.LIVE
    paid = True

    def __init__(self, settings: "Settings | None" = None, claims: Optional[frozenset[str]] = None):
        # The live paid endpoint is configured for liquidity in the default profile; a custom set of
        # served claims can be declared with the (optional) setting x402_evidence_claims="a,b".
        declared = getattr(settings, "x402_evidence_claims", None) if settings is not None else None
        if claims is not None:
            self.claims = frozenset(claims)
        elif declared:
            self.claims = frozenset(c.strip() for c in str(declared).split(",") if c.strip())
        else:
            self.claims = frozenset({"available_liquidity"})

    def is_configured(self, settings: "Settings") -> bool:
        return bool(settings.x402_enabled and settings.x402_evidence_url and settings.key("evidence_payer"))

    def family_for(self, url: str) -> str:
        return f"x402:{_host(url)}"

    async def fetch(self, requirement: Requirement, ctx: AcquisitionContext) -> RawEvidence:
        settings = ctx.settings
        if not self.is_configured(settings):
            raise AcquisitionError(ReasonCode.SOURCE_NOT_CONFIGURED, "x402-paid-evidence: x402_evidence_url / payer key not configured")
        base = str(settings.x402_evidence_url)
        params = {"claim": requirement.claim, "asset": ctx.asset}
        if settings.asset_token_address:
            params["token"] = settings.asset_token_address
        client = x402_client_for(ctx, ctx.http)
        result = await client.request(
            "GET",
            base,
            budget=ctx.budget,
            idempotency_key=ctx.acquisition_id(requirement.id, self.name),
            params=params,
            headers={"accept": "application/json"},
        )
        received_at = ctx.now()
        if not result.paid:
            raise_for_status(result.response, self.name)  # unpaid 2xx is accepted (free evidence)
        return evidence_json_to_raw(
            provider=self.name,
            source_type=self.source_type,
            mode=self.mode,
            requirement=requirement,
            ctx=ctx,
            result=result,
            request_descriptor={"method": "GET", "url": base, "params": params, "payment": "x402"},
            endpoint_id=f"x402:{base}",
            source_family=self.family_for(base),
            network=settings.x402_network,
            received_at=received_at,
        )


class X402ReferenceAdapter(SourceAdapter):
    name = "x402-reference"
    source_type = SourceType.API
    mode = SourceMode.SIMULATED
    claims = frozenset(DEFAULT_VALUES)
    paid = True

    def __init__(self, base_url: str = REFERENCE_BASE_URL):
        self.base_url = base_url.rstrip("/")

    def is_configured(self, settings: "Settings") -> bool:
        return bool(settings.x402_enabled and settings.x402_reference_enabled and settings.key("evidence_payer"))

    async def fetch(self, requirement: Requirement, ctx: AcquisitionContext) -> RawEvidence:
        settings = ctx.settings
        if not self.is_configured(settings):
            raise AcquisitionError(ReasonCode.SOURCE_NOT_CONFIGURED, "x402-reference: disabled or no payer key")
        if ctx.reference_http is None:
            raise AcquisitionError(ReasonCode.SOURCE_NOT_CONFIGURED, "x402-reference: no in-process reference client on the context")
        path = f"/evidence/{requirement.claim}"
        params = {"asset": ctx.asset}
        # A client with its own base_url (e.g. the API app with the server mounted under /_dev/x402)
        # gets a relative path; otherwise the in-process default host is used.
        url = path.lstrip("/") if str(ctx.reference_http.base_url) else self.base_url + path
        client = x402_client_for(ctx, ctx.reference_http)
        result = await client.request(
            "GET",
            url,
            budget=ctx.budget,
            idempotency_key=ctx.acquisition_id(requirement.id, self.name),
            params=params,
            headers={"accept": "application/json"},
        )
        received_at = ctx.now()
        if not result.paid:
            raise_for_status(result.response, self.name)
        return evidence_json_to_raw(
            provider=self.name,
            source_type=self.source_type,
            mode=self.mode,
            requirement=requirement,
            ctx=ctx,
            result=result,
            request_descriptor={"method": "GET", "url": f"x402-reference:{path}", "params": params, "payment": "x402"},
            endpoint_id=f"x402-reference:{path}",
            source_family=SIMULATED_FAMILY,  # the value comes from the simulated market
            network=settings.x402_network,
            received_at=received_at,
        )
