"""x402 v2 "exact"-scheme client used by paid evidence adapters.

Flow (one call to :meth:`X402Client.request`)::

    request ──► 200 ───────────────────────────────────────────────► free response (no payment)
       │
       └─► 402 ─► parse PaymentRequired (PAYMENT-REQUIRED header or JSON body; v1 bodies tolerated)
                 ─► select an "exact" requirement and validate it  ── fail ─► reject, NOTHING signed
                      network == settings.x402_network, asset == expected asset,
                      payTo in allowlist (if configured), amount <= per-request cap
                 ─► budget.try_reserve(quote)                       ── fail ─► BUDGET_EXCEEDED, NOTHING signed
                 ─► sign ONE EIP-3009 TransferWithAuthorization (random 32-byte nonce)
                 ─► resend with PAYMENT-SIGNATURE (v2) / X-PAYMENT (v1)
                      timeout ─► resend the SAME signed payload (never re-sign)
                 ─► parse PAYMENT-RESPONSE ─► SETTLED / VERIFIED / SIMULATED

Safety properties:

* **Validation and budget come before the signature.** Wrong network / asset / recipient or an
  over-budget quote raise :class:`AcquisitionError` without the key ever being used.
* **Idempotent retries.** An acquisition keeps exactly one signed authorization (``signed_store``
  keyed by the acquisition id). USDC authorization nonces are single-use on-chain, so re-sending the
  same payload after a timeout cannot cause a second payment. The payload stays in the store while its
  outcome is unknown (timeout, cancellation, 5xx) so a retry of the acquisition re-sends it; once the
  server gave a definitive answer (2xx, a decline, a 4xx) it is dropped, and a *new* acquisition with
  the same id would be a new, budget-gated purchase.
* **Conservative accounting.** Once a signed authorization has left the process it is a bearer
  instrument until ``validBefore``; if the outcome is unknown (timeout, cancellation, 5xx) its amount
  is committed to the budget as spent. Only an explicit rejection (402 after payment) releases it.
* **Payment never validates evidence.** The client returns the HTTP response; the body still goes
  through the adapter, the normalizer and the Decision Engine like any other observation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional

import httpx
from eth_utils import is_address, keccak

from ...domain.enums import PaymentStatus
from ...domain.models import EvidencePayment
from ...domain.reasons import ReasonCode
from ...evidence.base import AcquisitionError, EvidenceBudget
from .models import (
    SCHEME_EXACT,
    USDC_DECIMALS,
    PaymentPayload,
    PaymentRequired,
    PaymentRequirement,
    SettlementResponse,
    X402ProtocolError,
    atomic_to_decimal,
    chain_id_from_caip2,
    decimal_to_atomic,
    normalize_network,
    parse_payment_required,
)
from .signer import EvidencePayer

if TYPE_CHECKING:  # pragma: no cover
    from ...config import Settings

# EIP-3009 validity window: start slightly in the past to tolerate clock skew (same as the
# reference x402 EVM client), end after the server's maxTimeoutSeconds.
VALID_AFTER_SKEW_SECONDS = 600
# Upper bound of the authorization lifetime whatever maxTimeoutSeconds the server asks for: a signed
# EIP-3009 authorization is a bearer instrument until validBefore.
MAX_AUTHORIZATION_SECONDS = 600

SIGNED_STORE_KEY = "x402_signed"  # AcquisitionContext.extras key holding SignedPayment by acquisition id


@dataclass
class SignedPayment:
    """The single signed authorization of one acquisition (reused on retries, never re-signed)."""

    payload: PaymentPayload
    header_name: str
    header_value: str
    requirement: PaymentRequirement
    quoted: Decimal
    reference: str  # keccak256 of the exact header value
    payer: str
    reserved: Decimal = Decimal(0)  # budget reservation held for this authorization
    key: Optional[str] = None  # idempotency key (acquisition id) it is stored under
    sent: bool = False
    accounted: bool = False  # reservation already committed (spent) or released
    cost_reported: bool = False  # cost already attributed to an item or a failure

    def claim_cost(self) -> Decimal:
        """The quoted amount the first time it is attributed, 0 afterwards (no double counting
        when the same signed authorization is re-sent by a later attempt of the acquisition)."""
        if self.cost_reported:
            return Decimal(0)
        self.cost_reported = True
        return self.quoted

    def evidence_payment(self, status: PaymentStatus, *, settled: Decimal | None = None, reference: str | None = None) -> EvidencePayment:
        return EvidencePayment(
            protocol="x402",
            network=self.requirement.caip2_network,
            scheme=self.requirement.scheme,
            quoted_amount=self.quoted,
            settled_amount=settled,
            currency="USDC",
            payment_reference=reference or self.reference,
            status=status,
            payer=self.payer,
            pay_to=self.requirement.pay_to,
        )


@dataclass
class X402Result:
    """Outcome of a request that succeeded (2xx). ``payment`` is None when no payment was needed."""

    response: httpx.Response
    payment: Optional[EvidencePayment] = None
    cost: Decimal = Decimal(0)
    settlement: Optional[SettlementResponse] = None
    requirement: Optional[PaymentRequirement] = None
    attempts: int = 1  # number of paid sends (1 = no retry)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def paid(self) -> bool:
        return self.payment is not None


def payment_reference_for(header_value: str) -> str:
    """Deterministic reference of a signed payload: keccak256 of the exact header value."""
    return "0x" + keccak(header_value.encode("ascii")).hex()


class X402Client:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        payer: Optional[EvidencePayer],
        network: str,
        asset: str,
        max_per_request: Optional[Decimal],
        pay_to_allowlist: Iterable[str] = (),
        asset_decimals: int = USDC_DECIMALS,
        default_asset_name: str = "USDC",
        default_asset_version: str = "2",
        timeout: Optional[float] = None,
        send_retries: int = 1,
        clock: Callable[[], float] = time.time,
        signed_store: Optional[dict[str, SignedPayment]] = None,
    ):
        self.http = http
        self.payer = payer
        self.network = normalize_network(network) or network
        self.asset = asset
        self.max_per_request = max_per_request
        self.pay_to_allowlist = {a.strip().lower() for a in pay_to_allowlist if a and a.strip()}
        self.asset_decimals = asset_decimals
        self.default_asset_name = default_asset_name
        self.default_asset_version = default_asset_version
        self.timeout = timeout
        self.send_retries = max(0, int(send_retries))
        self.clock = clock
        self.signed_store: dict[str, SignedPayment] = signed_store if signed_store is not None else {}

    # ------------------------------------------------------------------------------------------
    @classmethod
    def from_settings(
        cls,
        settings: "Settings",
        http: httpx.AsyncClient,
        *,
        payer: Optional[EvidencePayer] = None,
        signed_store: Optional[dict[str, SignedPayment]] = None,
        network: Optional[str] = None,
        asset: Optional[str] = None,
        pay_to_allowlist: Optional[Iterable[str]] = None,
    ) -> "X402Client":
        if payer is None:
            key = settings.key("evidence_payer")
            payer = EvidencePayer(key) if key else None
        return cls(
            http,
            payer=payer,
            network=network or settings.x402_network,
            asset=asset or settings.x402_asset,
            max_per_request=settings.x402_max_per_request_usdc,
            pay_to_allowlist=settings.x402_pay_to_list if pay_to_allowlist is None else pay_to_allowlist,
            default_asset_name=settings.x402_asset_name,
            default_asset_version=settings.x402_asset_version,
            timeout=settings.http_timeout_seconds,
            send_retries=int(getattr(settings, "x402_send_retries", 1)),
            signed_store=signed_store,
        )

    # ------------------------------------------------------------------------------------------
    async def request(
        self,
        method: str,
        url: str,
        *,
        budget: Optional[EvidenceBudget],
        idempotency_key: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
        params: Optional[dict[str, Any]] = None,
        json_body: Any = None,
    ) -> X402Result:
        """Performs ``method url``, paying through x402 when the server answers 402.

        * first response not 402 -> returned as-is (unpaid; the caller maps its status);
        * 402 -> validate, reserve budget, sign once, resend; a 2xx paid response is returned with
          its :class:`EvidencePayment` and cost;
        * every other paid outcome raises :class:`AcquisitionError` carrying the payment (and the
          cost whenever money may have moved).

        ``idempotency_key`` (the acquisition id) binds at most one signed authorization to an
        acquisition: a later call with the same key re-sends that payload instead of signing again.
        """
        events: list[dict[str, Any]] = []
        existing = self.signed_store.get(idempotency_key) if idempotency_key else None
        if existing is not None:
            # A previous attempt of this acquisition already signed: resend that exact payload.
            events.append({"event": "x402.reuse_signed", "reference": existing.reference})
            return await self._send_paid(method, url, existing, budget, headers, params, json_body, events, reused=True)

        try:
            first = await self._send(method, url, headers, params, json_body)
        except httpx.TimeoutException as exc:
            raise AcquisitionError(ReasonCode.SOURCE_UNAVAILABLE, f"timeout before payment: {exc!r}") from exc
        except httpx.TransportError as exc:
            raise AcquisitionError(ReasonCode.SOURCE_UNAVAILABLE, f"transport error: {exc!r}") from exc

        if first.status_code != 402:
            return X402Result(response=first, events=events)

        # ---- 402: parse, select, validate — nothing is signed until all checks pass ------------
        try:
            required = parse_payment_required(first.headers, first.content)
        except X402ProtocolError as exc:
            raise AcquisitionError(
                ReasonCode.PAYMENT_FAILED,
                f"unparseable 402 payment requirements: {exc}",
                payment=EvidencePayment(status=PaymentStatus.REJECTED, network=self.network),
            ) from exc
        events.append({"event": "x402.quoted", "offers": len(required.accepts), "version": required.x402_version})
        requirement, quoted = self._select(required)

        if budget is not None and not await budget.try_reserve(quoted):
            raise AcquisitionError(
                ReasonCode.BUDGET_EXCEEDED,
                f"quote {quoted} USDC does not fit the remaining evidence budget {budget.remaining}",
                payment=self._unsigned_payment(requirement, quoted, PaymentStatus.BUDGET_EXCEEDED),
            )
        reserved = quoted if budget is not None else Decimal(0)

        try:
            signed = self._sign(required, requirement, quoted)
        except BaseException:
            # Nothing left the process: the reservation is simply returned.
            if budget is not None:
                await budget.release(reserved)
            raise
        signed.reserved = reserved
        if idempotency_key:
            signed.key = idempotency_key
            self.signed_store[idempotency_key] = signed
        events.append({"event": "x402.signed", "reference": signed.reference, "amount": str(quoted)})
        return await self._send_paid(method, url, signed, budget, headers, params, json_body, events)

    # ------------------------------------------------------------------------------------------
    def _select(self, required: PaymentRequired) -> tuple[PaymentRequirement, Decimal]:
        """Picks the first acceptable "exact" offer. Raises (unsigned) when none is acceptable."""
        if not required.accepts:
            raise AcquisitionError(
                ReasonCode.PAYMENT_FAILED,
                "402 without any payment offer" + (f" ({required.error})" if required.error else ""),
                payment=EvidencePayment(status=PaymentStatus.REJECTED, network=self.network),
            )
        problems: list[str] = []
        over_cap: Optional[tuple[PaymentRequirement, Decimal]] = None
        for req in required.accepts:
            reason = self._reject_reason(req)
            if reason is None:
                quoted = atomic_to_decimal(req.amount, self.asset_decimals)
                if self.max_per_request is not None and quoted > self.max_per_request:
                    over_cap = over_cap or (req, quoted)
                    problems.append(f"amount {quoted} USDC exceeds the per-request cap {self.max_per_request}")
                    continue
                return req, quoted
            problems.append(reason)
        if over_cap is not None:
            req, quoted = over_cap
            raise AcquisitionError(
                ReasonCode.BUDGET_EXCEEDED,
                "; ".join(problems),
                payment=self._unsigned_payment(req, quoted, PaymentStatus.BUDGET_EXCEEDED),
            )
        first = required.accepts[0]
        raise AcquisitionError(
            ReasonCode.PAYMENT_FAILED,
            "no acceptable x402 offer: " + "; ".join(problems),
            payment=self._unsigned_payment(first, atomic_to_decimal(first.amount, self.asset_decimals), PaymentStatus.REJECTED),
        )

    def _reject_reason(self, req: PaymentRequirement) -> Optional[str]:
        if req.scheme != SCHEME_EXACT:
            return f"scheme '{req.scheme}' is not supported (only 'exact')"
        if req.caip2_network != self.network:
            return f"network '{req.network}' does not match the configured network '{self.network}'"
        if req.asset.lower() != self.asset.lower():
            return f"asset {req.asset} is not the expected asset {self.asset}"
        if not is_address(req.pay_to):
            return f"payTo '{req.pay_to}' is not an EVM address"
        if self.pay_to_allowlist and req.pay_to.lower() not in self.pay_to_allowlist:
            return f"payTo {req.pay_to} is not in the allowlist"
        if req.max_timeout_seconds <= 0:
            return "maxTimeoutSeconds must be positive"
        try:
            chain_id_from_caip2(req.network)
        except X402ProtocolError as exc:
            return str(exc)
        return None

    def _unsigned_payment(self, req: PaymentRequirement, quoted: Decimal, status: PaymentStatus) -> EvidencePayment:
        return EvidencePayment(
            protocol="x402",
            network=req.caip2_network,
            scheme=req.scheme,
            quoted_amount=quoted,
            settled_amount=None,
            status=status,
            payer=self.payer.address if self.payer else None,
            pay_to=req.pay_to,
        )

    def _sign(self, required: PaymentRequired, req: PaymentRequirement, quoted: Decimal) -> SignedPayment:
        if self.payer is None:
            raise AcquisitionError(
                ReasonCode.PAYMENT_FAILED,
                "no evidence payer key configured",
                payment=self._unsigned_payment(req, quoted, PaymentStatus.REJECTED),
            )
        now = int(self.clock())
        chain_id = chain_id_from_caip2(req.network)
        authorization, signature = self.payer.authorize(
            to=req.pay_to,
            value=decimal_to_atomic(quoted, self.asset_decimals),
            valid_after=max(0, now - VALID_AFTER_SKEW_SECONDS),
            valid_before=now + min(req.max_timeout_seconds, MAX_AUTHORIZATION_SECONDS),
            name=str(req.extra.get("name") or self.default_asset_name),
            version=str(req.extra.get("version") or self.default_asset_version),
            chain_id=chain_id,
            asset=req.asset,
        )
        resource = required.resource
        if resource is None and req.resource:
            resource = {"url": req.resource}
        payload = PaymentPayload(
            x402_version=2 if required.x402_version >= 2 else 1,
            accepted=req.raw,
            signature=signature,
            authorization=authorization,
            resource=resource,
        )
        header_value = payload.header_value()
        return SignedPayment(
            payload=payload,
            header_name=payload.header_name,
            header_value=header_value,
            requirement=req,
            quoted=quoted,
            reference=payment_reference_for(header_value),
            payer=self.payer.address,
        )

    # ------------------------------------------------------------------------------------------
    async def _send(
        self, method: str, url: str, headers: Optional[dict[str, str]], params: Any, json_body: Any, extra: Optional[dict[str, str]] = None
    ) -> httpx.Response:
        merged = dict(headers or {})
        if extra:
            merged.update(extra)
        kwargs: dict[str, Any] = {"headers": merged, "params": params}
        if json_body is not None:
            kwargs["json"] = json_body
        if self.timeout is not None:
            kwargs["timeout"] = self.timeout
        return await self.http.request(method, url, **kwargs)

    def _forget(self, signed: SignedPayment) -> None:
        """Definitive outcome: the payload must not be re-sent by a later call."""
        if signed.key is not None and self.signed_store.get(signed.key) is signed:
            del self.signed_store[signed.key]

    @staticmethod
    async def _account(signed: SignedPayment, budget: Optional[EvidenceBudget], *, spent: bool) -> None:
        """Settles the budget reservation exactly once: commit (money may have moved) or release."""
        if signed.accounted:
            return
        signed.accounted = True
        if budget is None:
            return
        if spent:
            await budget.commit(signed.reserved)
        else:
            await budget.release(signed.reserved)

    async def _send_paid(
        self,
        method: str,
        url: str,
        signed: SignedPayment,
        budget: Optional[EvidenceBudget],
        headers: Optional[dict[str, str]],
        params: Any,
        json_body: Any,
        events: list[dict[str, Any]],
        *,
        reused: bool = False,
    ) -> X402Result:
        attempts = 0
        response: Optional[httpx.Response] = None
        last_error: Optional[BaseException] = None
        try:
            for _ in range(1 + self.send_retries):
                attempts += 1
                try:
                    signed.sent = True
                    response = await self._send(method, url, headers, params, json_body, {signed.header_name: signed.header_value})
                    break
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    # The payload may or may not have reached the server: resend the SAME signed
                    # authorization (single-use nonce), never a new one.
                    last_error = exc
                    events.append({"event": "x402.retry_same_payload", "attempt": attempts, "error": type(exc).__name__})
                    continue
        except BaseException:
            # Cancelled mid-flight (e.g. acquisition timeout): outcome unknown -> count as spent.
            await self._account(signed, budget, spent=True)
            raise

        if response is None:
            await self._account(signed, budget, spent=True)
            raise AcquisitionError(
                ReasonCode.SOURCE_UNAVAILABLE,
                f"paid request timed out after {attempts} attempt(s) with the same signed payload: {last_error!r}",
                cost_spent=signed.claim_cost(),
                payment=signed.evidence_payment(PaymentStatus.TIMEOUT),
            )

        try:
            settlement = SettlementResponse.from_headers(response.headers)
        except X402ProtocolError:
            settlement = None

        if response.status_code == 402:
            reason = self._decline_reason(response, settlement)
            if attempts > 1 or reused:
                # An earlier send of this same authorization may have been settled before the
                # connection dropped (the replay is then refused): outcome unknown, count as spent.
                await self._account(signed, budget, spent=True)
                self._forget(signed)  # re-sending it again would only be refused again
                raise AcquisitionError(
                    ReasonCode.PAYMENT_FAILED,
                    f"payment outcome unknown: re-sent authorization refused ({reason})",
                    cost_spent=signed.claim_cost(),
                    payment=signed.evidence_payment(PaymentStatus.TIMEOUT),
                )
            # The server explicitly declined the only send: nothing was settled.
            await self._account(signed, budget, spent=False)
            self._forget(signed)
            status = PaymentStatus.INSUFFICIENT_FUNDS if "insufficient" in reason.lower() else PaymentStatus.REJECTED
            raise AcquisitionError(
                ReasonCode.PAYMENT_FAILED,
                f"payment declined by the resource server: {reason}",
                payment=signed.evidence_payment(status),
            )

        await self._account(signed, budget, spent=True)
        if response.status_code < 500:
            self._forget(signed)  # 2xx / 4xx: definitive answer
        if not response.is_success:
            code = (
                ReasonCode.RATE_LIMITED
                if response.status_code == 429
                else ReasonCode.SOURCE_UNAVAILABLE
                if response.status_code >= 500
                else ReasonCode.INVALID_RESPONSE
            )
            settled = bool(settlement and settlement.success and settlement.transaction)
            raise AcquisitionError(
                code,
                f"paid request failed with HTTP {response.status_code}",
                cost_spent=signed.claim_cost(),
                payment=signed.evidence_payment(
                    PaymentStatus.SETTLED if settled else PaymentStatus.SETTLEMENT_FAILED,
                    settled=signed.quoted if settled else None,
                    reference=settlement.transaction if settled and settlement else None,
                ),
            )

        if settlement is not None and not settlement.success:
            payment = signed.evidence_payment(PaymentStatus.SETTLEMENT_FAILED)
        elif settlement is not None and settlement.transaction:
            payment = signed.evidence_payment(PaymentStatus.SETTLED, settled=signed.quoted, reference=settlement.transaction)
        elif settlement is not None and (settlement.settlement or "").upper() == "SIMULATED":
            # Local reference server: signature verified, nothing settled on-chain.
            payment = signed.evidence_payment(PaymentStatus.SIMULATED, reference=settlement.reference or signed.reference)
        else:
            # Resource served after the server verified the payment, but no on-chain settlement proof.
            payment = signed.evidence_payment(PaymentStatus.VERIFIED, reference=(settlement.reference if settlement else None))
        events.append({"event": "x402.paid", "status": payment.status.value, "attempts": attempts})
        return X402Result(
            response=response,
            payment=payment,
            cost=signed.claim_cost(),
            settlement=settlement,
            requirement=signed.requirement,
            attempts=attempts,
            events=events,
        )

    @staticmethod
    def _decline_reason(response: httpx.Response, settlement: Optional[SettlementResponse]) -> str:
        if settlement is not None and settlement.error_reason:
            return settlement.error_reason
        try:
            parsed = parse_payment_required(response.headers, response.content)
            if parsed.error:
                return parsed.error
        except X402ProtocolError:
            pass
        return "402 after payment"
