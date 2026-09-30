"""Immutable decision snapshots and deterministic replay (Document 03 §47-49, Document 04 §80).

A snapshot captures *every* input of ``evaluate`` as canonical JSON: the frozen Evidence Contract,
the exact Evidence Set (items + acquisition failures), the evaluation timestamp, the engine
version and the permitted-source registry. Replay never queries current market conditions: it
parses the snapshot and re-runs the engine.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from eth_utils import keccak

from ..canonical import canonicalize
from ..canonical.canonicalize import canonical_json
from ..domain.models import Decision, EvidenceContract, EvidenceSet
from .engine import evaluate


def parse_time(value: str | datetime) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"timestamp must be timezone-aware: {value!r}")
    return dt


def make_snapshot(
    contract: EvidenceContract,
    evidence_set: EvidenceSet,
    evaluation_time: datetime,
    engine_version: str,
    permitted_sources: dict[str, set[str]] | None = None,
) -> str:
    return canonical_json(
        {
            "contract": contract,
            "engine_version": engine_version,
            "evaluation_time": evaluation_time,
            "evidence_set": evidence_set,
            "permitted_sources": None
            if permitted_sources is None
            else {k: sorted(v) for k, v in permitted_sources.items()},
        }
    )


def load_snapshot(snapshot: str) -> dict[str, Any]:
    data = canonicalize.loads(snapshot)
    permitted = data.get("permitted_sources")
    return {
        "contract": EvidenceContract.model_validate(data["contract"]),
        "evidence_set": EvidenceSet.model_validate(data["evidence_set"]),
        "evaluation_time": parse_time(data["evaluation_time"]),
        "engine_version": data["engine_version"],
        "permitted_sources": None if permitted is None else {k: set(v) for k, v in permitted.items()},
    }


def replay_snapshot(snapshot: str) -> Decision:
    s = load_snapshot(snapshot)
    return evaluate(
        s["contract"],
        s["evidence_set"],
        s["evaluation_time"],
        engine_version=s["engine_version"],
        permitted_sources=s["permitted_sources"],
    )


def snapshot_hash(snapshot: str) -> str:
    return "0x" + keccak(b"EVIDENCE_GATE_SNAPSHOT_V1\x00" + snapshot.encode("ascii")).hex()
