"""RawEvidence -> EvidenceItem (Document 03 §14-16, §76-85).

The normalizer is the strict numeric boundary between untrusted provider payloads and the engine:

* ``int`` / finite ``Decimal`` pass unchanged (``bool`` is *not* a number and stays as-is);
* numeric strings matching ``^-?\\d+(\\.\\d+)?$`` become ``Decimal`` (APIs such as DexScreener return
  prices as strings). The match is a *full* match over ASCII digits only: ``"123\\n"``, ``" 12"``,
  ``"+5"``, ``"1e6"``, ``"1,000"`` or non-ASCII digits stay raw;
* ``float`` becomes ``Decimal(repr(f))`` (shortest round-trip, never binary noise); NaN/Infinity
  become non-finite Decimals so the engine flags INVALID_VALUE;
* anything else (``"1,340,000"``, ``"1e6"``, ``"N/A"``, ``None``, dicts, ...) is kept RAW so the engine
  marks the item INVALID. Malformed evidence stays visible; it is never dropped and never coerced
  into something that could PASS.

The normalizer never evaluates a requirement: it only shapes provenance, integrity hashes and the
value. It never raises on a malformed value.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from ..canonical.canonicalize import canonical_bytes
from ..canonical.hashes import REQUEST_DOMAIN, RESPONSE_DOMAIN, raw_hash
from ..domain.claims import get_claim
from ..domain.models import (
    EvidenceClaim,
    EvidenceEconomic,
    EvidenceIntegrity,
    EvidenceItem,
    EvidenceSource,
    EvidenceTemporal,
    Requirement,
)
from .base import RawEvidence

NUMERIC_STRING_RE = re.compile(r"-?[0-9]+(\.[0-9]+)?")  # used with fullmatch (ASCII digits only)
EVIDENCE_ID_DOMAIN = b"EVIDENCE_GATE_EVIDENCE_ID_V1"


def parse_numeric(value: Any) -> Any:
    """Strict boundary parsing. Returns a Decimal/int for well-formed numbers, the raw value otherwise."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return Decimal("NaN")
        if math.isinf(value):
            return Decimal("Infinity") if value > 0 else Decimal("-Infinity")
        return Decimal(repr(value))
    if isinstance(value, str) and NUMERIC_STRING_RE.fullmatch(value):
        try:
            return Decimal(value)
        except InvalidOperation:  # pragma: no cover - regex guarantees parseability
            return value
    return value


def request_hash(raw: RawEvidence) -> str:
    return raw_hash(REQUEST_DOMAIN, canonical_bytes(raw.request_descriptor))


def response_hash(raw: RawEvidence) -> str:
    return raw_hash(RESPONSE_DOMAIN, bytes(raw.response_body))


def make_evidence_id(acquisition_id: str, response_hash_: str) -> str:
    """Deterministic id: ``ev_`` + 16 hex of keccak(domain || acquisition_id | response_hash)."""
    return "ev_" + raw_hash(EVIDENCE_ID_DOMAIN, f"{acquisition_id}|{response_hash_}".encode("utf-8"))[2:18]


def _normalize_value(raw: RawEvidence, requirement: Requirement) -> Any:
    spec = get_claim(requirement.claim) or get_claim(raw.claim)
    if spec is not None and not spec.numeric:
        return raw.value  # booleans / identifiers are validated by the engine as-is
    return parse_numeric(raw.value)


def normalize(
    raw: RawEvidence,
    *,
    requirement: Requirement,
    contract_id: str,
    evidence_id: str | None = None,
    acquisition_id: str,
) -> EvidenceItem:
    """Builds the EvidenceItem the engine will judge.

    ``evidence_id`` may be passed explicitly; when omitted (None) it is derived deterministically from
    ``acquisition_id`` and the response hash via :func:`make_evidence_id`.
    """
    req_hash = request_hash(raw)
    resp_hash = response_hash(raw)
    eid = evidence_id or make_evidence_id(acquisition_id, resp_hash)
    # Build the temporal block without coercion: a malformed timestamp string must stay a string
    # (pydantic would otherwise turn "1700000000" into a datetime and hide the provider's format).
    temporal = EvidenceTemporal.model_construct(observed_at=raw.observed_at, received_at=raw.received_at)
    return EvidenceItem(
        evidence_id=eid,
        contract_id=contract_id,
        requirement_id=requirement.id,
        claim=EvidenceClaim(name=raw.claim, value=_normalize_value(raw, requirement), unit=raw.unit),
        temporal=temporal,
        source=EvidenceSource(
            provider=raw.provider,
            source_type=raw.source_type,
            mode=raw.mode,
            endpoint_id=raw.endpoint_id,
            upstream_source=raw.upstream_source,
            source_family=raw.source_family,
            network=raw.network,
            captured_at=raw.captured_at,
        ),
        integrity=EvidenceIntegrity(request_hash=req_hash, response_hash=resp_hash, chain_ref=raw.chain_ref),
        economic=EvidenceEconomic(cost=Decimal(raw.cost), currency="USDC", payment=raw.payment),
        derivation=raw.derivation,
        acquisition_id=acquisition_id,
    )
