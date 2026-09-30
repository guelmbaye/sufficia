"""Helpers shared by adapters that pay for evidence through x402.

The payer and the per-acquisition signed-payload store live in ``AcquisitionContext.extras`` so every
adapter of one run shares them: one signer (``sign_count`` is observable) and at most one signed
authorization per acquisition id (retries reuse it, never re-sign).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import httpx

from ...domain.enums import SourceMode, SourceType
from ...domain.models import Requirement
from ...domain.reasons import ReasonCode
from ...payments.x402 import SIGNED_STORE_KEY, EvidencePayer, X402Client, X402Result
from ..base import AcquisitionContext, AcquisitionError, RawEvidence
from ._common import parse_json_bytes, parse_timestamp, raise_for_status

PAYER_KEY = "x402_payer"


def get_payer(ctx: AcquisitionContext) -> Optional[EvidencePayer]:
    payer = ctx.extras.get(PAYER_KEY)
    if payer is None:
        key = ctx.settings.key("evidence_payer")
        if not key:
            return None
        payer = EvidencePayer(key)
        ctx.extras[PAYER_KEY] = payer
    return payer


def x402_client_for(
    ctx: AcquisitionContext, http: httpx.AsyncClient, *, network: Optional[str] = None, asset: Optional[str] = None
) -> X402Client:
    store = ctx.extras.setdefault(SIGNED_STORE_KEY, {})
    return X402Client.from_settings(ctx.settings, http, payer=get_payer(ctx), signed_store=store, network=network, asset=asset)


def paid_extra(result: X402Result) -> dict[str, Any]:
    """Keyword arguments that attach the payment to an AcquisitionError raised after paying."""
    return {"cost_spent": result.cost, "payment": result.payment}


def evidence_json_to_raw(
    *,
    provider: str,
    source_type: SourceType,
    mode: SourceMode,
    requirement: Requirement,
    ctx: AcquisitionContext,
    result: X402Result,
    request_descriptor: dict[str, Any],
    endpoint_id: str,
    source_family: str,
    network: Optional[str],
    received_at: datetime,
) -> RawEvidence:
    """Parses the SUFFICIA paid-evidence JSON format served by x402 endpoints::

        {"claim", "value", "unit", "observed_at", "asset", "provider", "upstream_source"?}

    Structural problems (not JSON, no ``value``/``claim``, evidence about another asset) are
    acquisition failures that still carry the cost and payment. A present-but-malformed ``value``,
    ``unit`` or ``observed_at`` is passed through RAW so the engine marks the item INVALID: a paid
    observation is never dropped and payment never makes evidence valid.
    """
    response = result.response
    extra = paid_extra(result)
    raise_for_status(response, provider, **extra)
    data = parse_json_bytes(response.content, provider, **extra)
    if not isinstance(data, dict):
        raise AcquisitionError(ReasonCode.INVALID_RESPONSE, f"{provider} evidence is not a JSON object", **extra)
    for key in ("claim", "value"):
        if key not in data:
            raise AcquisitionError(ReasonCode.MISSING_FIELD, f"{provider} evidence has no '{key}'", **extra)
    served_asset = data.get("asset")
    if served_asset is not None and not _same_asset(served_asset, ctx):
        raise AcquisitionError(
            ReasonCode.INVALID_RESPONSE, f"{provider} served evidence for asset '{served_asset}', not '{ctx.asset}'", **extra
        )
    upstream = data.get("upstream_source") or data.get("provider")
    return RawEvidence(
        provider=provider,
        source_type=source_type,
        mode=mode,
        claim=data["claim"] if isinstance(data["claim"], str) else str(data["claim"]),
        value=data["value"],
        unit=data.get("unit") if isinstance(data.get("unit"), str) or data.get("unit") is None else str(data.get("unit")),
        observed_at=parse_timestamp(data.get("observed_at")),
        received_at=received_at,
        request_descriptor=request_descriptor,
        response_body=response.content,
        endpoint_id=endpoint_id,
        upstream_source=str(upstream) if upstream is not None else None,
        source_family=source_family,
        network=network,
        cost=Decimal(result.cost),
        payment=result.payment,
    )


def _same_asset(served: Any, ctx: AcquisitionContext) -> bool:
    text = str(served).strip().lower()
    if text == ctx.asset.lower():
        return True
    token = getattr(ctx.settings, "asset_token_address", None)
    return bool(token) and text == str(token).lower()
