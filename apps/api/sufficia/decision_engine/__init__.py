"""Deterministic Decision Engine: Evidence -> Authority."""

from .authorization import AuthorityError, build_authorization, unix_seconds_floor, verify_binding
from .engine import ENGINE_VERSION, SUPPORTED_ENGINE_VERSIONS, UnsupportedEngineVersion, evaluate
from .snapshot import load_snapshot, make_snapshot, replay_snapshot, snapshot_hash
from .validator import InvalidContractError, validate_contract

__all__ = [
    "ENGINE_VERSION",
    "SUPPORTED_ENGINE_VERSIONS",
    "AuthorityError",
    "InvalidContractError",
    "UnsupportedEngineVersion",
    "build_authorization",
    "evaluate",
    "load_snapshot",
    "make_snapshot",
    "replay_snapshot",
    "snapshot_hash",
    "unix_seconds_floor",
    "validate_contract",
    "verify_binding",
]
