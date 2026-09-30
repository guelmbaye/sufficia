"""LOCAL x402 reference resource server — for tests and demos only.

What it is: a small FastAPI app that implements the *resource-server* side of x402 v2 ("exact" scheme,
EIP-3009 USDC authorizations) well enough to exercise a real client end to end, in-process:

* ``GET /evidence/{claim}?asset=TOKEN-X`` without a valid payment -> ``402`` with the payment
  requirements, both base64-JSON in ``PAYMENT-REQUIRED`` and as the JSON body (price
  ``x402_reference_price_usdc``, payTo ``x402_reference_pay_to``, network ``x402_network``, asset
  ``x402_asset``, extra ``{name, version}``);
* with ``PAYMENT-SIGNATURE`` (or v1 ``X-PAYMENT``) it **really verifies** the payload: it decodes it,
  checks the accepted requirement matches the offered one, recovers the EIP-712 signer and requires it
  to equal ``authorization.from``, requires ``to == payTo``, ``value >= price``,
  ``validAfter <= now < validBefore`` and an unused nonce (in-memory replay set);
* on success it returns ``200`` with SUFFICIA paid-evidence JSON read from the SIMULATED market
  (provider "x402-reference", ``observed_at`` = now) and a ``PAYMENT-RESPONSE`` header::

      {"success": true, "transaction": null, "settlement": "SIMULATED",
       "reference": keccak256(payment header), "network": ..., "payer": ...}

What it is NOT: a facilitator. It never submits ``transferWithAuthorization`` on-chain, never moves
funds and never claims to: ``transaction`` is always ``null`` and ``settlement`` is ``"SIMULATED"``.
It does not check the payer's USDC balance. Evidence it serves comes from the simulated market and is
labelled SIMULATED by the consuming adapter. Real paid evidence uses ``x402_evidence_url`` and a real
facilitator (Monad: ``x402_facilitator``).
"""

from __future__ import annotations

import threading
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable, Optional

from eth_utils import is_address, keccak, to_checksum_address
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from ..canonical.canonicalize import format_datetime, format_decimal
from ..domain.claims import get_claim
from ..evidence.base import utcnow
from ..evidence.simulated_market import DEFAULT_ASSET, MARKET, SimulatedMarket
from ..payments.x402.models import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PAYMENT_SIGNATURE_HEADER,
    SCHEME_EXACT,
    V1_PAYMENT_HEADER,
    X402_VERSION,
    PaymentPayload,
    X402ProtocolError,
    b64_json_encode,
    chain_id_from_caip2,
    decimal_to_atomic,
    normalize_network,
)
from ..payments.x402.signer import recover_authorizer

if TYPE_CHECKING:  # pragma: no cover
    from ..config import Settings

PROVIDER = "x402-reference"
MAX_TIMEOUT_SECONDS = 60


class ReferenceState:
    """In-memory server state: used nonces (replay protection) and counters for tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.used_nonces: set[tuple[str, str]] = set()  # (payer lower, nonce lower)
        self.verified_payments = 0
        self.rejections: list[str] = []

    def consume_nonce(self, payer: str, nonce: str) -> bool:
        key = (payer.lower(), nonce.lower())
        with self._lock:
            if key in self.used_nonces:
                return False
            self.used_nonces.add(key)
            self.verified_payments += 1
            return True

    def reject(self, reason: str) -> None:
        with self._lock:
            self.rejections.append(reason)


def create_x402_reference_app(
    settings: "Settings",
    *,
    market: Optional[SimulatedMarket] = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    """Builds the LOCAL reference resource server (see module docstring). ``app.state.x402`` exposes
    the :class:`ReferenceState` (used nonces, counters)."""
    market = market or MARKET
    network = normalize_network(settings.x402_network) or settings.x402_network
    chain_id = chain_id_from_caip2(network)
    asset = to_checksum_address(settings.x402_asset)
    pay_to = to_checksum_address(settings.x402_reference_pay_to)
    price = Decimal(settings.x402_reference_price_usdc)
    price_atomic = decimal_to_atomic(price)
    extra = {"name": settings.x402_asset_name, "version": settings.x402_asset_version}

    app = FastAPI(
        title="SUFFICIA local x402 reference server",
        description="LOCAL reference implementation of an x402 v2 resource server for tests and demos. "
        "Signatures are verified; nothing is settled on-chain (settlement = SIMULATED).",
        version="1.0.0",
    )
    state = ReferenceState()
    app.state.x402 = state

    def requirement_for(claim: str, url: str) -> dict[str, Any]:
        return {
            "scheme": SCHEME_EXACT,
            "network": network,
            "amount": str(price_atomic),
            "asset": asset,
            "payTo": pay_to,
            "maxTimeoutSeconds": MAX_TIMEOUT_SECONDS,
            "extra": dict(extra),
            "resource": url,
            "description": f"SUFFICIA simulated evidence: {claim}",
            "mimeType": "application/json",
        }

    def payment_required(claim: str, url: str, error: str) -> Response:
        body = {
            "x402Version": X402_VERSION,
            "error": error,
            "resource": {"url": url, "description": f"SUFFICIA simulated evidence: {claim}", "mimeType": "application/json"},
            "accepts": [requirement_for(claim, url)],
        }
        return JSONResponse(status_code=402, content=body, headers={PAYMENT_REQUIRED_HEADER: b64_json_encode(body)})

    def verify(header_value: str, claim: str, url: str) -> tuple[Optional[str], Optional[PaymentPayload]]:
        """Returns (error, payload). ``error`` is None when the payment is valid; nonce not yet consumed."""
        try:
            payload = PaymentPayload.from_header(header_value)
        except X402ProtocolError as exc:
            return f"invalid_payload: {exc}", None
        offered = requirement_for(claim, url)
        acc = payload.accepted
        if acc.get("scheme") != SCHEME_EXACT:
            return "unsupported_scheme", payload
        if normalize_network(acc.get("network")) != network:
            return "invalid_network", payload
        if payload.x402_version >= 2:
            if str(acc.get("asset", "")).lower() != asset.lower():
                return "invalid_asset", payload
            if str(acc.get("payTo", "")).lower() != pay_to.lower():
                return "invalid_pay_to", payload
            if str(acc.get("amount")) != offered["amount"]:
                return "requirement_mismatch", payload
        auth = payload.authorization
        if not (is_address(auth.from_address) and is_address(auth.to)):
            return "invalid_authorization_addresses", payload
        if auth.to.lower() != pay_to.lower():
            return "invalid_recipient", payload
        if auth.value < price_atomic:
            return "insufficient_amount", payload
        now = int(clock())
        if auth.valid_after > now:
            return "authorization_not_yet_valid", payload
        if now >= auth.valid_before:
            return "authorization_expired", payload
        try:
            signer = recover_authorizer(
                name=str(extra["name"]),
                version=str(extra["version"]),
                chain_id=chain_id,
                verifying_contract=asset,
                authorization=auth,
                signature=payload.signature,
            )
        except Exception:  # noqa: BLE001 - malformed signature
            return "invalid_signature", payload
        if signer.lower() != auth.from_address.lower():
            return "invalid_signature", payload
        return None, payload

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "kind": "local-x402-reference",
            "settlement": "SIMULATED",
            "network": network,
            "asset": asset,
            "pay_to": pay_to,
            "price_usdc": format_decimal(price),
            "verified_payments": state.verified_payments,
        }

    @app.get("/evidence/{claim}")
    async def evidence(claim: str, request: Request) -> Response:
        asset_name = request.query_params.get("asset") or DEFAULT_ASSET
        url = str(request.url)
        if get_claim(claim) is None:
            return JSONResponse(status_code=404, content={"error": f"unknown claim '{claim}'"})
        header_value = request.headers.get(PAYMENT_SIGNATURE_HEADER) or request.headers.get(V1_PAYMENT_HEADER)
        if not header_value:
            return payment_required(claim, url, "payment required")
        error, payload = verify(header_value, claim, url)
        if error is not None or payload is None:
            state.reject(error or "invalid_payload")
            return payment_required(claim, url, error or "invalid_payload")
        if not state.consume_nonce(payload.authorization.from_address, payload.authorization.nonce):
            state.reject("nonce_already_used")
            return payment_required(claim, url, "nonce_already_used")

        observation = market.get(asset_name, claim)
        reference = "0x" + keccak(header_value.encode("ascii")).hex()
        settlement = {
            "success": True,
            "transaction": None,
            "settlement": "SIMULATED",
            "reference": reference,
            "network": network,
            "payer": to_checksum_address(payload.authorization.from_address),
        }
        headers = {PAYMENT_RESPONSE_HEADER: b64_json_encode(settlement)}
        if observation is None:
            return JSONResponse(status_code=404, content={"error": f"no simulated value for {claim}"}, headers=headers)
        spec = get_claim(claim)
        body = {
            "claim": claim,
            "value": format_decimal(observation.value),
            "unit": spec.unit if spec else None,
            "observed_at": format_datetime(utcnow()),
            "asset": observation.asset,
            "provider": PROVIDER,
            "upstream_source": "simulated-market",
        }
        return JSONResponse(status_code=200, content=body, headers=headers)

    return app
