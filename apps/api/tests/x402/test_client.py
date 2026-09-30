"""X402Client safety properties: nothing is signed before validation + budget; one signature per
acquisition; retries resend the same payload; payment never makes evidence valid."""

from __future__ import annotations

import time
from decimal import Decimal

import httpx
import pytest

from sufficia.devtools.x402_reference import create_x402_reference_app
from sufficia.domain.enums import PaymentStatus
from sufficia.domain.reasons import ReasonCode
from sufficia.evidence.base import AcquisitionError, EvidenceBudget
from sufficia.payments.x402 import (
    PAYMENT_REQUIRED_HEADER,
    PAYMENT_SIGNATURE_HEADER,
    EvidencePayer,
    PaymentPayload,
    X402Client,
    b64_json_encode,
    recover_authorizer,
)

PAYER_KEY = "0x" + "55" * 32
USDC = "0x534b2f3A21130d7a60830c2Df862319e593943A3"
PAY_TO = "0x000000000000000000000000000000000000dEaD"
URL = "https://paid.test/evidence"


def offer(**kw):
    base = {
        "scheme": "exact",
        "network": "eip155:10143",
        "amount": "20000",
        "asset": USDC,
        "payTo": PAY_TO,
        "maxTimeoutSeconds": 60,
        "extra": {"name": "USDC", "version": "2"},
    }
    base.update(kw)
    return base


def required_body(*offers, version=2):
    return {"x402Version": version, "accepts": list(offers) or [offer()], "error": "payment required"}


class Server:
    """Scriptable x402 resource server for MockTransport. Records every request."""

    def __init__(self, required=None, *, in_header=True, paid=None):
        self.required = required or required_body()
        self.in_header = in_header
        self.requests: list[httpx.Request] = []
        self.paid = paid or (lambda req: httpx.Response(200, json={"claim": "available_liquidity", "value": "600000", "unit": "USD"}))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if PAYMENT_SIGNATURE_HEADER not in request.headers and "X-PAYMENT" not in request.headers:
            headers = {PAYMENT_REQUIRED_HEADER: b64_json_encode(self.required)} if self.in_header else {}
            return httpx.Response(402, json=self.required if not self.in_header else {}, headers=headers)
        return self.paid(request)

    @property
    def payment_headers(self):
        return [r.headers.get(PAYMENT_SIGNATURE_HEADER) or r.headers.get("X-PAYMENT") for r in self.requests if PAYMENT_SIGNATURE_HEADER in r.headers or "X-PAYMENT" in r.headers]


def make_client(handler, *, payer=None, allowlist=(), cap=Decimal("0.10"), network="eip155:10143", asset=USDC, store=None, timeout=None):
    return X402Client(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        payer=payer or EvidencePayer(PAYER_KEY),
        network=network,
        asset=asset,
        max_per_request=cap,
        pay_to_allowlist=allowlist,
        signed_store=store,
        timeout=timeout,
    )


def test_happy_path_signs_once_and_reports_verified(h):
    server = Server()
    client = make_client(server)
    budget = EvidenceBudget(Decimal("0.25"))
    result = h.run(client.request("GET", URL, budget=budget, idempotency_key="acq_1"))
    assert result.response.status_code == 200 and result.paid
    assert client.payer.sign_count == 1
    assert result.cost == Decimal("0.02") and budget.spent == Decimal("0.02") and budget.remaining == Decimal("0.23")
    assert result.payment.status == PaymentStatus.VERIFIED  # no PAYMENT-RESPONSE: served but no settlement proof
    assert result.payment.network == "eip155:10143" and result.payment.scheme == "exact"
    assert result.payment.payer == client.payer.address and result.payment.pay_to == PAY_TO
    # the payload is the v2 shape and the signature recovers to the payer
    payload = PaymentPayload.from_header(server.payment_headers[0])
    assert payload.x402_version == 2 and payload.accepted["payTo"] == PAY_TO
    assert payload.authorization.value == 20000 and payload.authorization.to.lower() == PAY_TO.lower()
    signer = recover_authorizer(name="USDC", version="2", chain_id=10143, verifying_contract=USDC, authorization=payload.authorization, signature=payload.signature)
    assert signer == client.payer.address
    assert len(payload.authorization.nonce) == 66
    assert payload.authorization.valid_after <= int(time.time()) < payload.authorization.valid_before


def test_requirements_in_body_and_v1_bodies_are_understood(h):
    v1 = {"x402Version": 1, "accepts": [{**{k: v for k, v in offer().items() if k != "amount"}, "maxAmountRequired": "20000", "network": "monad-testnet"}]}
    server = Server(v1, in_header=False)
    client = make_client(server)
    result = h.run(client.request("GET", URL, budget=EvidenceBudget(Decimal(1))))
    assert result.paid and result.response.status_code == 200
    assert server.requests[-1].headers.get("X-PAYMENT")  # v1 header
    assert result.payment.network == "eip155:10143"


def test_free_resource_is_not_paid(h):
    client = make_client(lambda r: httpx.Response(200, json={"ok": True}))
    result = h.run(client.request("GET", URL, budget=EvidenceBudget(Decimal(1))))
    assert not result.paid and result.cost == 0 and client.payer.sign_count == 0


# ---- nothing is signed when a check fails -------------------------------------------------------


@pytest.mark.parametrize(
    "bad_offer, allowlist, fragment",
    [
        (offer(network="eip155:1"), (), "network"),
        (offer(network="base-sepolia"), (), "network"),
        (offer(asset="0x0000000000000000000000000000000000000001"), (), "asset"),
        (offer(payTo="0x000000000000000000000000000000000000bEEF"), (PAY_TO,), "allowlist"),
        (offer(scheme="upto"), (), "scheme"),
        (offer(payTo="not-an-address"), (), "payTo"),
    ],
)
def test_wrong_network_asset_recipient_scheme_rejected_without_signature(h, bad_offer, allowlist, fragment):
    server = Server(required_body(bad_offer))
    client = make_client(server, allowlist=allowlist)
    budget = EvidenceBudget(Decimal("0.25"))
    with pytest.raises(AcquisitionError) as e:
        h.run(client.request("GET", URL, budget=budget, idempotency_key="acq"))
    assert e.value.code == ReasonCode.PAYMENT_FAILED
    assert fragment in e.value.detail
    assert e.value.payment.status == PaymentStatus.REJECTED
    assert e.value.cost_spent == 0
    assert client.payer.sign_count == 0
    assert server.payment_headers == []  # never sent a payment
    assert budget.spent == 0 and budget.remaining == Decimal("0.25")  # nothing reserved


def test_allowlisted_recipient_is_accepted_case_insensitively(h):
    client = make_client(Server(), allowlist=(PAY_TO.lower(),))
    assert h.run(client.request("GET", URL, budget=EvidenceBudget(Decimal(1)))).paid


def test_quote_above_remaining_budget_is_never_signed(h):
    server = Server(required_body(offer(amount="50000")))  # 0.05 USDC
    client = make_client(server)
    budget = EvidenceBudget(Decimal("0.25"), already_spent=Decimal("0.22"))  # 0.03 remaining
    with pytest.raises(AcquisitionError) as e:
        h.run(client.request("GET", URL, budget=budget, idempotency_key="acq"))
    assert e.value.code == ReasonCode.BUDGET_EXCEEDED
    assert e.value.payment.status == PaymentStatus.BUDGET_EXCEEDED and e.value.payment.quoted_amount == Decimal("0.05")
    assert e.value.cost_spent == 0
    assert client.payer.sign_count == 0 and server.payment_headers == []
    assert budget.spent == Decimal("0.22") and budget.remaining == Decimal("0.03")


def test_quote_above_per_request_cap_is_never_signed(h):
    server = Server(required_body(offer(amount="150000")))  # 0.15 > 0.10 cap
    client = make_client(server)
    with pytest.raises(AcquisitionError) as e:
        h.run(client.request("GET", URL, budget=EvidenceBudget(Decimal(10))))
    assert e.value.code == ReasonCode.BUDGET_EXCEEDED and client.payer.sign_count == 0


def test_first_acceptable_offer_is_selected(h):
    server = Server(required_body(offer(network="eip155:8453"), offer(amount="10000")))
    client = make_client(server)
    result = h.run(client.request("GET", URL, budget=EvidenceBudget(Decimal(1))))
    assert result.cost == Decimal("0.01") and client.payer.sign_count == 1


def test_no_payer_key_is_payment_failed_without_signing(h):
    server = Server()
    client = X402Client(httpx.AsyncClient(transport=httpx.MockTransport(server)), payer=None, network="eip155:10143", asset=USDC, max_per_request=Decimal("0.1"))
    budget = EvidenceBudget(Decimal(1))
    with pytest.raises(AcquisitionError) as e:
        h.run(client.request("GET", URL, budget=budget))
    assert e.value.code == ReasonCode.PAYMENT_FAILED and budget.remaining == Decimal(1)


def test_unparseable_402_is_payment_failed(h):
    client = make_client(lambda r: httpx.Response(402, content=b"pay me"))
    with pytest.raises(AcquisitionError) as e:
        h.run(client.request("GET", URL, budget=EvidenceBudget(Decimal(1))))
    assert e.value.code == ReasonCode.PAYMENT_FAILED and client.payer.sign_count == 0


# ---- idempotency: timeouts after sending re-use the same signed payload --------------------------


def test_timeout_after_send_retries_with_same_payload(h):
    attempts = {"paid": 0}

    def paid(request):
        attempts["paid"] += 1
        if attempts["paid"] == 1:
            raise httpx.ReadTimeout("lost response", request=request)
        return httpx.Response(200, json={"claim": "available_liquidity", "value": "1"})

    server = Server(paid=paid)
    client = make_client(server)
    budget = EvidenceBudget(Decimal("0.25"))
    result = h.run(client.request("GET", URL, budget=budget, idempotency_key="acq_1"))
    assert result.paid and result.attempts == 2
    assert client.payer.sign_count == 1
    headers = server.payment_headers
    assert len(headers) == 2 and headers[0] == headers[1]  # byte-identical payload (same nonce)
    assert budget.spent == Decimal("0.02")
    assert any(e["event"] == "x402.retry_same_payload" for e in result.events)


def test_timeouts_exhausted_count_as_spent_and_later_call_reuses_payload(h):
    def always_timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    server = Server(paid=always_timeout)
    store: dict = {}
    client = make_client(server, store=store)
    budget = EvidenceBudget(Decimal("0.25"))
    with pytest.raises(AcquisitionError) as e:
        h.run(client.request("GET", URL, budget=budget, idempotency_key="acq_1"))
    assert e.value.code == ReasonCode.SOURCE_UNAVAILABLE
    assert e.value.payment.status == PaymentStatus.TIMEOUT
    assert e.value.cost_spent == Decimal("0.02")  # conservative: the money may have moved
    assert budget.spent == Decimal("0.02")
    first_payload = server.payment_headers[0]
    assert "acq_1" in store  # outcome unknown: kept for a re-send

    # a later attempt of the same acquisition re-sends the SAME authorization, never re-signs
    server.paid = lambda r: httpx.Response(200, json={"claim": "available_liquidity", "value": "1"})
    result = h.run(client.request("GET", URL, budget=budget, idempotency_key="acq_1"))
    assert result.paid and client.payer.sign_count == 1
    assert server.payment_headers[-1] == first_payload
    assert result.cost == 0  # already attributed to the timed-out attempt: no double counting
    assert budget.spent == Decimal("0.02")
    assert "acq_1" not in store  # definitive answer: forgotten


def test_replay_refused_after_lost_response_is_unknown_not_free(h):
    """Timeout, then the server refuses the re-sent (already consumed) nonce: count it as spent."""
    calls = {"n": 0}

    def paid(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("lost", request=request)
        return httpx.Response(402, json={"x402Version": 2, "accepts": [offer()], "error": "nonce_already_used"})

    client = make_client(Server(paid=paid))
    budget = EvidenceBudget(Decimal("0.25"))
    with pytest.raises(AcquisitionError) as e:
        h.run(client.request("GET", URL, budget=budget, idempotency_key="acq_1"))
    assert e.value.code == ReasonCode.PAYMENT_FAILED and e.value.payment.status == PaymentStatus.TIMEOUT
    assert e.value.cost_spent == Decimal("0.02") and budget.spent == Decimal("0.02")
    assert client.payer.sign_count == 1


def test_explicit_decline_releases_the_reservation(h):
    server = Server(paid=lambda r: httpx.Response(402, json={"x402Version": 2, "accepts": [offer()], "error": "insufficient_funds"}))
    client = make_client(server)
    budget = EvidenceBudget(Decimal("0.25"))
    with pytest.raises(AcquisitionError) as e:
        h.run(client.request("GET", URL, budget=budget, idempotency_key="acq"))
    assert e.value.code == ReasonCode.PAYMENT_FAILED
    assert e.value.payment.status == PaymentStatus.INSUFFICIENT_FUNDS
    assert e.value.cost_spent == 0 and budget.spent == 0 and budget.remaining == Decimal("0.25")


def test_settlement_header_with_transaction_is_settled(h):
    settle = b64_json_encode({"success": True, "transaction": "0x" + "ab" * 32, "network": "eip155:10143", "payer": "0x1"})
    server = Server(paid=lambda r: httpx.Response(200, json={"value": 1}, headers={"PAYMENT-RESPONSE": settle}))
    result = h.run(make_client(server).request("GET", URL, budget=EvidenceBudget(Decimal(1))))
    assert result.payment.status == PaymentStatus.SETTLED
    assert result.payment.settled_amount == Decimal("0.02") and result.payment.payment_reference == "0x" + "ab" * 32


def test_failed_settlement_is_reported(h):
    settle = b64_json_encode({"success": False, "errorReason": "insufficient_funds", "network": "eip155:10143"})
    server = Server(paid=lambda r: httpx.Response(200, json={"value": 1}, headers={"PAYMENT-RESPONSE": settle}))
    result = h.run(make_client(server).request("GET", URL, budget=EvidenceBudget(Decimal(1))))
    assert result.payment.status == PaymentStatus.SETTLEMENT_FAILED


def test_paid_then_server_error_keeps_cost(h):
    server = Server(paid=lambda r: httpx.Response(503))
    budget = EvidenceBudget(Decimal(1))
    with pytest.raises(AcquisitionError) as e:
        h.run(make_client(server).request("GET", URL, budget=budget, idempotency_key="k"))
    assert e.value.code == ReasonCode.SOURCE_UNAVAILABLE and e.value.cost_spent == Decimal("0.02")
    assert e.value.payment.status == PaymentStatus.SETTLEMENT_FAILED and budget.spent == Decimal("0.02")


# ---- end-to-end against the reference server (signature verified server-side) -----------------------


def test_end_to_end_against_reference_server(h):
    settings = h.make_settings()
    app = create_x402_reference_app(settings)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://x402-reference.local") as http:
            client = X402Client.from_settings(settings, http)
            budget = EvidenceBudget(Decimal("0.25"), Decimal("0.10"))
            result = await client.request("GET", "/evidence/available_liquidity", params={"asset": "TOKEN-X"}, budget=budget, idempotency_key="acq_e2e")
            return client, budget, result

    client, budget, result = h.run(go())
    assert result.response.status_code == 200
    assert result.response.json()["value"] == "92440"
    assert result.payment.status == PaymentStatus.SIMULATED
    assert result.settlement.transaction is None and result.settlement.settlement == "SIMULATED"
    assert result.payment.payment_reference == result.settlement.reference
    assert app.state.x402.verified_payments == 1  # the server really verified the EIP-712 signature
    assert client.payer.sign_count == 1 and budget.spent == Decimal("0.02")
