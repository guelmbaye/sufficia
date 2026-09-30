"""Local x402 reference server: it genuinely verifies EIP-3009 signatures and never claims settlement."""

from __future__ import annotations

import time
from decimal import Decimal

import httpx
import pytest

from sufficia.devtools.x402_reference import create_x402_reference_app
from sufficia.payments.x402 import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PAYMENT_SIGNATURE_HEADER,
    V1_PAYMENT_HEADER,
    EvidencePayer,
    PaymentPayload,
    PaymentRequired,
    b64_json_decode,
    b64_json_encode,
)

PAYER_KEY = "0x" + "55" * 32
OTHER_KEY = "0x" + "66" * 32
BASE = "http://x402-reference.local"


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


async def _quote(client, claim="available_liquidity"):
    r = await client.get(f"/evidence/{claim}", params={"asset": "TOKEN-X"})
    assert r.status_code == 402
    return r


def _sign(settings, requirement: dict, *, key=PAYER_KEY, to=None, value=None, valid_after=None, valid_before=None, nonce=None, version=2):
    payer = EvidencePayer(key)
    now = int(time.time())
    auth, sig = payer.authorize(
        to=to or requirement["payTo"],
        value=int(requirement["amount"]) if value is None else value,
        valid_after=now - 60 if valid_after is None else valid_after,
        valid_before=now + 60 if valid_before is None else valid_before,
        name=requirement["extra"]["name"],
        version=requirement["extra"]["version"],
        chain_id=10143,
        asset=requirement["asset"],
        nonce=nonce,
    )
    return PaymentPayload(x402_version=version, accepted=requirement, signature=sig, authorization=auth), payer


def test_unpaid_request_gets_402_with_header_and_body(h):
    settings = h.make_settings()
    app = create_x402_reference_app(settings)

    async def go():
        async with _client(app) as c:
            return await _quote(c)

    r = h.run(go())
    header = b64_json_decode(r.headers[PAYMENT_REQUIRED_HEADER])
    assert header == r.json()
    required = PaymentRequired.from_dict(header)
    assert required.x402_version == 2
    offer = required.accepts[0]
    assert offer.scheme == "exact" and offer.network == "eip155:10143"
    assert offer.amount == 20000  # 0.02 USDC, 6 decimals
    assert offer.asset.lower() == settings.x402_asset.lower()
    assert offer.pay_to.lower() == settings.x402_reference_pay_to.lower()
    assert offer.extra == {"name": "USDC", "version": "2"}


def test_valid_payment_is_verified_and_settlement_is_simulated(h):
    settings = h.make_settings()
    app = create_x402_reference_app(settings)

    async def go():
        async with _client(app) as c:
            q = await _quote(c)
            payload, payer = _sign(settings, q.json()["accepts"][0])
            r = await c.get("/evidence/available_liquidity", params={"asset": "TOKEN-X"}, headers={PAYMENT_SIGNATURE_HEADER: payload.header_value()})
            return r, payer

    r, payer = h.run(go())
    assert r.status_code == 200
    body = r.json()
    assert body["claim"] == "available_liquidity" and body["value"] == "92440" and body["unit"] == "USD"
    assert body["provider"] == "x402-reference" and body["asset"] == "TOKEN-X" and body["observed_at"].endswith("Z")
    settlement = b64_json_decode(r.headers[PAYMENT_RESPONSE_HEADER])
    assert settlement["success"] is True
    assert settlement["transaction"] is None and settlement["settlement"] == "SIMULATED"
    assert settlement["payer"] == payer.address and settlement["network"] == "eip155:10143"
    assert settlement["reference"].startswith("0x") and len(settlement["reference"]) == 66
    assert app.state.x402.verified_payments == 1


def test_replayed_nonce_is_rejected(h):
    settings = h.make_settings()
    app = create_x402_reference_app(settings)

    async def go():
        async with _client(app) as c:
            q = await _quote(c)
            payload, _ = _sign(settings, q.json()["accepts"][0])
            hdr = {PAYMENT_SIGNATURE_HEADER: payload.header_value()}
            first = await c.get("/evidence/asset_price", params={"asset": "TOKEN-X"}, headers=hdr)
            second = await c.get("/evidence/asset_price", params={"asset": "TOKEN-X"}, headers=hdr)
            return first, second

    first, second = h.run(go())
    assert first.status_code == 200
    assert second.status_code == 402 and second.json()["error"] == "nonce_already_used"
    assert app.state.x402.verified_payments == 1


@pytest.mark.parametrize(
    "mutate, error",
    [
        (lambda req, s: {"key": OTHER_KEY, "tamper_from": True}, "invalid_signature"),
        (lambda req, s: {"to": "0x000000000000000000000000000000000000bEEF"}, "invalid_recipient"),
        (lambda req, s: {"value": 19999}, "insufficient_amount"),
        (lambda req, s: {"valid_before": int(time.time()) - 1}, "authorization_expired"),
        (lambda req, s: {"valid_after": int(time.time()) + 3600}, "authorization_not_yet_valid"),
        (lambda req, s: {"accepted_patch": {"network": "eip155:1"}}, "invalid_network"),
        (lambda req, s: {"accepted_patch": {"amount": "1"}}, "requirement_mismatch"),
        (lambda req, s: {"accepted_patch": {"asset": "0x0000000000000000000000000000000000000001"}}, "invalid_asset"),
    ],
)
def test_invalid_payments_are_rejected(h, mutate, error):
    settings = h.make_settings()
    app = create_x402_reference_app(settings)

    async def go():
        async with _client(app) as c:
            q = await _quote(c)
            requirement = q.json()["accepts"][0]
            opts = mutate(requirement, settings)
            tamper = opts.pop("tamper_from", False)
            patch = opts.pop("accepted_patch", None)
            payload, _ = _sign(settings, requirement, **opts)
            data = payload.to_dict()
            if tamper:  # signed by OTHER_KEY but claims to be from the real payer
                data["payload"]["authorization"]["from"] = EvidencePayer(PAYER_KEY).address
            if patch:
                data["accepted"] = {**data["accepted"], **patch}
            return await c.get("/evidence/available_liquidity", params={"asset": "TOKEN-X"}, headers={PAYMENT_SIGNATURE_HEADER: b64_json_encode(data)})

    r = h.run(go())
    assert r.status_code == 402
    assert r.json()["error"] == error
    assert app.state.x402.verified_payments == 0


def test_garbage_payment_header_and_unknown_claim(h):
    app = create_x402_reference_app(h.make_settings())

    async def go():
        async with _client(app) as c:
            bad = await c.get("/evidence/available_liquidity", headers={PAYMENT_SIGNATURE_HEADER: "%%%not-base64"})
            unknown = await c.get("/evidence/moon_phase")
            health = await c.get("/health")
            return bad, unknown, health

    bad, unknown, health = h.run(go())
    assert bad.status_code == 402 and bad.json()["error"].startswith("invalid_payload")
    assert unknown.status_code == 404
    assert health.json()["settlement"] == "SIMULATED" and health.json()["kind"] == "local-x402-reference"


def test_v1_x_payment_header_is_accepted(h):
    settings = h.make_settings()
    app = create_x402_reference_app(settings)

    async def go():
        async with _client(app) as c:
            q = await _quote(c)
            payload, _ = _sign(settings, q.json()["accepts"][0], version=1)
            assert "accepted" not in payload.to_dict()  # v1 wire shape
            return await c.get("/evidence/asset_price", params={"asset": "TOKEN-X"}, headers={V1_PAYMENT_HEADER: payload.header_value()})

    r = h.run(go())
    assert r.status_code == 200 and r.json()["value"] == "0.428"


def test_server_uses_simulated_market_values(h):
    from sufficia.evidence.simulated_market import MARKET

    MARKET.set("TOKEN-X", "available_liquidity", Decimal("512345.5"))
    settings = h.make_settings()
    app = create_x402_reference_app(settings)

    async def go():
        async with _client(app) as c:
            q = await _quote(c)
            payload, _ = _sign(settings, q.json()["accepts"][0])
            return await c.get("/evidence/available_liquidity", params={"asset": "token-x"}, headers={PAYMENT_SIGNATURE_HEADER: payload.header_value()})

    assert h.run(go()).json()["value"] == "512345.5"
