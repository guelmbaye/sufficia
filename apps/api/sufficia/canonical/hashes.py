"""Domain-separated commitments (Document 03 §37-50, §77; Document 04 §16-18).

Offchain objects (contract, evidence, receipt, intent, decision) are hashed as
``keccak256(domain_tag || canonical_json)``. Objects that the EvidenceGate contract must recompute
(action hash, authorization id) use the exact ABI encodings of ``EvidenceGate.sol`` so the backend
and the chain agree byte for byte.
"""

from __future__ import annotations

from typing import Any, Iterable

from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address

from .canonicalize import canonical_bytes

# ---- domain tags ----------------------------------------------------------------------------
CONTRACT_DOMAIN = b"EVIDENCE_GATE_CONTRACT_V1"
EVIDENCE_DOMAIN = b"EVIDENCE_GATE_EVIDENCE_V1"
EVIDENCE_SET_DOMAIN = b"EVIDENCE_GATE_EVIDENCE_SET_V1"
INTENT_DOMAIN = b"EVIDENCE_GATE_INTENT_V1"
DECISION_DOMAIN = b"EVIDENCE_GATE_DECISION_V1"
RECEIPT_DOMAIN = b"EVIDENCE_GATE_RECEIPT_V1"
NONCE_DOMAIN = b"EVIDENCE_GATE_NONCE_V1"
AUDIT_DOMAIN = b"EVIDENCE_GATE_AUDIT_V1"
REQUEST_DOMAIN = b"EVIDENCE_GATE_REQUEST_V1"
RESPONSE_DOMAIN = b"EVIDENCE_GATE_RESPONSE_V1"

# Onchain domains: keccak256 of the tag string, as in EvidenceGate.sol.
ACTION_DOMAIN_ONCHAIN = keccak(text="EVIDENCE_GATE_ACTION_V1")
AUTH_DOMAIN_ONCHAIN = keccak(text="EVIDENCE_GATE_AUTH_V1")

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

ACTION_HASH_TYPES = [
    "bytes32",
    "uint256",
    "address",
    "address",
    "uint256",
    "bytes32",
    "address",
    "uint256",
    "address",
    "uint256",
    "address",
    "uint64",
    "bytes32",
]
AUTH_ID_TYPES = ["bytes32", "bytes32", "bytes32", "bytes32", "address", "bytes32"]


def hx(b: bytes) -> str:
    return "0x" + b.hex()


def unhex32(value: str) -> bytes:
    raw = bytes.fromhex(value[2:] if value.startswith("0x") else value)
    if len(raw) != 32:
        raise ValueError(f"expected 32 bytes, got {len(raw)}")
    return raw


def unhex(value: str) -> bytes:
    if not value or value == "0x":
        return b""
    return bytes.fromhex(value[2:] if value.startswith("0x") else value)


def domain_hash(domain: bytes, obj: Any) -> str:
    return hx(keccak(domain + b"\x00" + canonical_bytes(obj)))


def raw_hash(domain: bytes, payload: bytes) -> str:
    return hx(keccak(domain + b"\x00" + payload))


# ---- offchain commitments ---------------------------------------------------------------------


def contract_semantics(contract: Any) -> dict:
    """The authority-relevant content of an Evidence Contract (ids excluded).

    Two contracts with the same semantics share a hash; any change to a threshold, operator,
    freshness window, policy, limit or TTL changes it (Document 06 §40).
    """
    dump = contract.model_dump(mode="python")
    dump.pop("contract_id", None)
    dump.pop("intent_id", None)
    dump["requirements"] = sorted(dump["requirements"], key=lambda r: r["id"])
    for r in dump["requirements"]:
        r.pop("label", None)  # presentation only
        if r.get("values") is not None:
            r["values"] = sorted(r["values"])
    return dump


def contract_hash(contract: Any) -> str:
    return domain_hash(CONTRACT_DOMAIN, contract_semantics(contract))


def intent_hash(intent: Any) -> str:
    return domain_hash(INTENT_DOMAIN, intent)


def evidence_item_hash(item: Any) -> str:
    return domain_hash(EVIDENCE_DOMAIN, item)


def evidence_root(item_hashes: Iterable[str]) -> str:
    """Order-independent commitment to a *set* of evidence items (duplicates collapse)."""
    unique = sorted({h.lower() for h in item_hashes})
    payload = b"".join(unhex32(h) for h in unique)
    return hx(keccak(EVIDENCE_SET_DOMAIN + b"\x00" + len(unique).to_bytes(4, "big") + payload))


def decision_inputs_commitment(acquisition_failures: Any, permitted_sources: Any) -> str:
    """Commits to every decision input that is not in the contract or the evidence root.

    Acquisition failures (and their costs) and the permitted-source registry both change outcomes,
    so two decisions that differ in either must never share a fingerprint / decision id.
    """
    failures = sorted(
        (f.model_dump(mode="python") if hasattr(f, "model_dump") else dict(f) for f in acquisition_failures),
        key=lambda f: canonical_bytes(f),
    )
    permitted = None
    if permitted_sources is not None:
        permitted = {str(k): sorted(v) for k, v in permitted_sources.items()}
    return domain_hash(DECISION_DOMAIN, {"acquisition_failures": failures, "permitted_sources": permitted})


def decision_fingerprint(
    contract_hash_: str, evidence_root_: str, evaluated_at: str, engine_version: str, inputs_commitment: str
) -> str:
    return hx(
        keccak(
            DECISION_DOMAIN
            + b"\x00"
            + unhex32(contract_hash_)
            + unhex32(evidence_root_)
            + unhex32(inputs_commitment)
            + evaluated_at.encode("ascii")
            + b"|"
            + engine_version.encode("ascii")
        )
    )


def authorization_nonce(fingerprint: str) -> str:
    return hx(keccak(NONCE_DOMAIN + b"\x00" + unhex32(fingerprint)))


def receipt_hash(receipt_body: Any) -> str:
    return domain_hash(RECEIPT_DOMAIN, receipt_body)


# ---- onchain-reproducible commitments ---------------------------------------------------------


def action_hash(
    *,
    chain_id: int,
    gate_address: str,
    target: str,
    value: int,
    data: str,
    asset_in: str,
    max_amount_in: int,
    asset_out: str,
    min_amount_out: int,
    recipient: str,
    deadline: int,
    nonce: str,
) -> str:
    """Mirror of ``EvidenceGate.computeActionHash`` (single abi.encode of 13 words)."""
    encoded = abi_encode(
        ACTION_HASH_TYPES,
        [
            ACTION_DOMAIN_ONCHAIN,
            int(chain_id),
            to_checksum_address(gate_address),
            to_checksum_address(target),
            int(value),
            keccak(unhex(data)),
            to_checksum_address(asset_in),
            int(max_amount_in),
            to_checksum_address(asset_out),
            int(min_amount_out),
            to_checksum_address(recipient),
            int(deadline),
            unhex32(nonce),
        ],
    )
    return hx(keccak(encoded))


def canonical_action_hash(action: Any) -> str:
    return action_hash(
        chain_id=action.chain_id,
        gate_address=action.gate_address,
        target=action.target,
        value=action.value,
        data=action.data,
        asset_in=action.asset_in,
        max_amount_in=action.max_amount_in,
        asset_out=action.asset_out,
        min_amount_out=action.min_amount_out,
        recipient=action.recipient,
        deadline=action.deadline,
        nonce=action.nonce,
    )


def authorization_id(contract_hash_: str, evidence_root_: str, action_hash_: str, executor: str, nonce: str) -> str:
    """Mirror of ``EvidenceGate.computeAuthorizationId``."""
    encoded = abi_encode(
        AUTH_ID_TYPES,
        [
            AUTH_DOMAIN_ONCHAIN,
            unhex32(contract_hash_),
            unhex32(evidence_root_),
            unhex32(action_hash_),
            to_checksum_address(executor),
            unhex32(nonce),
        ],
    )
    return hx(keccak(encoded))


def onchain_intent_id(intent_hash_: str) -> str:
    """The bytes32 intent identifier registered onchain is the intent commitment itself."""
    return intent_hash_
