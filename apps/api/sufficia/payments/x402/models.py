"""x402 wire models (protocol v2 "exact" scheme, with tolerant parsing of v1 bodies).

Wire format summary (x402 v2, as used by the Monad facilitator):

* server -> client ``402 Payment Required`` carries a *PaymentRequired* object, either base64-JSON in
  the ``PAYMENT-REQUIRED`` header or as the JSON body::

      {"x402Version": 2, "error"?: str, "resource"?: {"url", "description", "mimeType"},
       "accepts": [{"scheme": "exact", "network": "eip155:10143", "amount": "20000",
                    "asset": "0x534b...", "payTo": "0x...", "maxTimeoutSeconds": 60,
                    "extra": {"name": "USDC", "version": "2"}}]}

  v1 bodies use ``maxAmountRequired`` instead of ``amount`` and short network names.
* client -> server retry carries a *PaymentPayload*, base64-JSON in ``PAYMENT-SIGNATURE`` (v2) or
  ``X-PAYMENT`` (v1).
* server -> client success carries a *SettlementResponse*, base64-JSON in ``PAYMENT-RESPONSE`` (v2) or
  ``X-PAYMENT-RESPONSE`` (v1): ``{"success", "transaction"|"txHash", "network", "payer", "errorReason"}``.

Amounts on the wire are *atomic* integer strings (USDC: 6 decimals). Internally, quotes are Decimal
USDC so they can be compared with budgets without floats.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

X402_VERSION = 2

PAYMENT_REQUIRED_HEADER = "PAYMENT-REQUIRED"
PAYMENT_SIGNATURE_HEADER = "PAYMENT-SIGNATURE"
PAYMENT_RESPONSE_HEADER = "PAYMENT-RESPONSE"
# x402 v1 header names (accepted for robustness).
V1_PAYMENT_HEADER = "X-PAYMENT"
V1_PAYMENT_RESPONSE_HEADER = "X-PAYMENT-RESPONSE"

SCHEME_EXACT = "exact"
USDC_DECIMALS = 6

# v1 used short network names; normalize the ones relevant to us to CAIP-2.
NETWORK_ALIASES: dict[str, str] = {
    "monad-testnet": "eip155:10143",
    "monad": "eip155:143",
    "base-sepolia": "eip155:84532",
    "base": "eip155:8453",
}


class X402ProtocolError(ValueError):
    """A message on the wire could not be parsed as x402."""


# ---- encoding helpers --------------------------------------------------------------------------


def b64_json_encode(obj: Any) -> str:
    data = json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.b64encode(data).decode("ascii")


def b64_json_decode(value: str) -> Any:
    text = value.strip()
    raw: bytes | None = None
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            raw = decoder(text + "=" * (-len(text) % 4))
            break
        except (binascii.Error, ValueError):
            continue
    if raw is None:
        raise X402ProtocolError("header is not valid base64")
    try:
        return json.loads(raw.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise X402ProtocolError(f"header is not base64 JSON: {exc}") from exc


def normalize_network(network: str | None) -> str | None:
    if network is None:
        return None
    n = str(network).strip()
    return NETWORK_ALIASES.get(n.lower(), n)


def chain_id_from_caip2(network: str) -> int:
    """``eip155:10143`` -> 10143. Only EVM (eip155) networks can be signed with EIP-3009."""
    namespace, _, reference = (normalize_network(network) or "").partition(":")
    if namespace != "eip155" or not reference.isdigit():
        raise X402ProtocolError(f"unsupported network '{network}' (expected CAIP-2 eip155:<chainId>)")
    return int(reference)


def atomic_to_decimal(amount: int, decimals: int = USDC_DECIMALS) -> Decimal:
    return Decimal(int(amount)).scaleb(-decimals)


def decimal_to_atomic(amount: Decimal, decimals: int = USDC_DECIMALS) -> int:
    scaled = Decimal(amount).scaleb(decimals)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{amount} has more than {decimals} decimals")
    return int(scaled)


def parse_atomic_amount(value: Any) -> int:
    """Strict atomic amount: a non-negative integer or integer string. Anything else is rejected."""
    if isinstance(value, bool):
        raise X402ProtocolError("amount must be an integer")
    if isinstance(value, int):
        atomic = value
    elif isinstance(value, str) and value.strip().isdigit():
        atomic = int(value.strip())
    elif isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        atomic = int(value)
    else:
        raise X402ProtocolError(f"amount '{value}' is not an atomic integer")
    if atomic < 0:
        raise X402ProtocolError("amount must be >= 0")
    return atomic


# ---- messages ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class PaymentRequirement:
    """One entry of ``accepts``. ``raw`` is kept verbatim so it can be echoed back as ``accepted``."""

    scheme: str
    network: str  # as offered on the wire
    amount: int  # atomic units
    asset: str
    pay_to: str
    max_timeout_seconds: int
    extra: dict[str, Any]
    raw: dict[str, Any]
    resource: Optional[str] = None
    description: Optional[str] = None

    @property
    def caip2_network(self) -> str:
        return normalize_network(self.network) or ""

    @classmethod
    def from_dict(cls, data: Any) -> "PaymentRequirement":
        if not isinstance(data, dict):
            raise X402ProtocolError("payment requirement must be an object")
        amount_field = data.get("amount", data.get("maxAmountRequired"))
        if amount_field is None:
            raise X402ProtocolError("payment requirement has no amount")
        for key in ("scheme", "network", "asset", "payTo"):
            if not isinstance(data.get(key), str) or not data.get(key):
                raise X402ProtocolError(f"payment requirement field '{key}' is missing")
        timeout = data.get("maxTimeoutSeconds", 60)
        try:
            timeout_int = int(timeout)
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise X402ProtocolError("maxTimeoutSeconds must be an integer") from exc
        extra = data.get("extra") or {}
        if not isinstance(extra, dict):
            raise X402ProtocolError("extra must be an object")
        return cls(
            scheme=data["scheme"],
            network=data["network"],
            amount=parse_atomic_amount(amount_field),
            asset=data["asset"],
            pay_to=data["payTo"],
            max_timeout_seconds=timeout_int,
            extra=dict(extra),
            raw=_jsonable(data),
            resource=data.get("resource") if isinstance(data.get("resource"), str) else None,
            description=data.get("description") if isinstance(data.get("description"), str) else None,
        )


@dataclass(frozen=True)
class PaymentRequired:
    x402_version: int
    accepts: list[PaymentRequirement]
    error: Optional[str] = None
    resource: Optional[dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Any) -> "PaymentRequired":
        if not isinstance(data, dict) or not isinstance(data.get("accepts"), list):
            raise X402ProtocolError("PaymentRequired must be an object with an 'accepts' list")
        version_raw = data.get("x402Version", 1)
        try:
            version = int(version_raw)
        except (TypeError, ValueError) as exc:
            raise X402ProtocolError("x402Version must be an integer") from exc
        accepts: list[PaymentRequirement] = []
        for entry in data["accepts"]:
            try:
                accepts.append(PaymentRequirement.from_dict(entry))
            except X402ProtocolError:
                continue  # an unparseable offer is simply not selectable
        # v1 offers carry `maxAmountRequired`; treat such bodies as v1 even if the version is missing.
        if any("maxAmountRequired" in (e or {}) for e in data["accepts"] if isinstance(e, dict)) and "x402Version" not in data:
            version = 1
        resource = data.get("resource") if isinstance(data.get("resource"), dict) else None
        error = data.get("error") if isinstance(data.get("error"), str) else None
        return cls(x402_version=version, accepts=accepts, error=error, resource=_jsonable(resource) if resource else None)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"x402Version": self.x402_version, "accepts": [a.raw for a in self.accepts]}
        if self.error is not None:
            out["error"] = self.error
        if self.resource is not None:
            out["resource"] = self.resource
        return out


@dataclass(frozen=True)
class TransferAuthorization:
    """EIP-3009 ``TransferWithAuthorization`` message (values are ints; nonce is 0x-prefixed 32 bytes)."""

    from_address: str
    to: str
    value: int
    valid_after: int
    valid_before: int
    nonce: str

    def to_wire(self) -> dict[str, str]:
        return {
            "from": self.from_address,
            "to": self.to,
            "value": str(self.value),
            "validAfter": str(self.valid_after),
            "validBefore": str(self.valid_before),
            "nonce": self.nonce,
        }

    @classmethod
    def from_wire(cls, data: Any) -> "TransferAuthorization":
        if not isinstance(data, dict):
            raise X402ProtocolError("authorization must be an object")
        try:
            nonce = str(data["nonce"])
            if not (nonce.startswith("0x") and len(nonce) == 66):
                raise X402ProtocolError("nonce must be 32 bytes hex")
            int(nonce, 16)
            return cls(
                from_address=str(data["from"]),
                to=str(data["to"]),
                value=parse_atomic_amount(data["value"]),
                valid_after=parse_atomic_amount(data["validAfter"]),
                valid_before=parse_atomic_amount(data["validBefore"]),
                nonce=nonce,
            )
        except KeyError as exc:
            raise X402ProtocolError(f"authorization field {exc} is missing") from exc
        except ValueError as exc:
            raise X402ProtocolError(f"malformed authorization: {exc}") from exc


@dataclass(frozen=True)
class PaymentPayload:
    x402_version: int
    accepted: dict[str, Any]
    signature: str
    authorization: TransferAuthorization
    resource: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        inner = {"signature": self.signature, "authorization": self.authorization.to_wire()}
        if self.x402_version >= 2:
            out: dict[str, Any] = {"x402Version": self.x402_version, "accepted": self.accepted, "payload": inner}
            if self.resource is not None:
                out["resource"] = self.resource
            return out
        # v1 shape
        return {
            "x402Version": 1,
            "scheme": self.accepted.get("scheme", SCHEME_EXACT),
            "network": self.accepted.get("network"),
            "payload": inner,
        }

    def header_value(self) -> str:
        return b64_json_encode(self.to_dict())

    @property
    def header_name(self) -> str:
        return PAYMENT_SIGNATURE_HEADER if self.x402_version >= 2 else V1_PAYMENT_HEADER

    @classmethod
    def from_header(cls, value: str) -> "PaymentPayload":
        data = b64_json_decode(value)
        if not isinstance(data, dict):
            raise X402ProtocolError("payment payload must be an object")
        payload = data.get("payload")
        if not isinstance(payload, dict) or not isinstance(payload.get("signature"), str):
            raise X402ProtocolError("payment payload has no signature")
        try:
            version = int(data.get("x402Version", 1))
        except (TypeError, ValueError) as exc:
            raise X402ProtocolError("x402Version must be an integer") from exc
        accepted = data.get("accepted")
        if accepted is None and version < 2:
            accepted = {"scheme": data.get("scheme"), "network": data.get("network")}
        if not isinstance(accepted, dict):
            raise X402ProtocolError("payment payload has no 'accepted' requirement")
        resource = data.get("resource") if isinstance(data.get("resource"), dict) else None
        return cls(
            x402_version=version,
            accepted=_jsonable(accepted),
            signature=payload["signature"],
            authorization=TransferAuthorization.from_wire(payload.get("authorization")),
            resource=_jsonable(resource) if resource else None,
        )


@dataclass(frozen=True)
class SettlementResponse:
    success: bool
    transaction: Optional[str] = None
    network: Optional[str] = None
    payer: Optional[str] = None
    error_reason: Optional[str] = None
    settlement: Optional[str] = None  # "SIMULATED" from the local reference server
    reference: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> "SettlementResponse":
        if not isinstance(data, dict):
            raise X402ProtocolError("settlement response must be an object")
        tx = data.get("transaction") or data.get("txHash")
        return cls(
            success=bool(data.get("success")),
            transaction=str(tx) if tx else None,
            network=data.get("network") if isinstance(data.get("network"), str) else None,
            payer=data.get("payer") if isinstance(data.get("payer"), str) else None,
            error_reason=(data.get("errorReason") or data.get("error_reason")) or None,
            settlement=data.get("settlement") if isinstance(data.get("settlement"), str) else None,
            reference=data.get("reference") if isinstance(data.get("reference"), str) else None,
            raw=_jsonable(data),
        )

    @classmethod
    def from_headers(cls, headers: Any) -> Optional["SettlementResponse"]:
        value = headers.get(PAYMENT_RESPONSE_HEADER) or headers.get(V1_PAYMENT_RESPONSE_HEADER)
        if not value:
            return None
        return cls.from_dict(b64_json_decode(value))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"success": self.success, "transaction": self.transaction, "network": self.network}
        if self.payer is not None:
            out["payer"] = self.payer
        if self.error_reason is not None:
            out["errorReason"] = self.error_reason
        if self.settlement is not None:
            out["settlement"] = self.settlement
        if self.reference is not None:
            out["reference"] = self.reference
        return out


def parse_payment_required(headers: Any, body: bytes) -> PaymentRequired:
    """Parses a 402 response: ``PAYMENT-REQUIRED`` header first, then the JSON body."""
    header = headers.get(PAYMENT_REQUIRED_HEADER)
    if header:
        try:
            return PaymentRequired.from_dict(b64_json_decode(header))
        except X402ProtocolError:
            pass  # fall back to the body
    try:
        data = json.loads(body.decode("utf-8"), parse_float=Decimal) if body else None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise X402ProtocolError("402 response carries no parseable payment requirements") from exc
    return PaymentRequired.from_dict(data)


def _jsonable(obj: Any) -> Any:
    """Converts Decimals from tolerant parsing back to JSON-safe values (ints stay ints)."""
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else str(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    return obj
