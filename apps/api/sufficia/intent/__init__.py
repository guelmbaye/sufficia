"""Intent compiler: natural-language instruction -> authoritative Evidence Contract.

    from sufficia.intent import compile_intent, parse_intent

The deterministic parser is the default compiler and the reference for intent preservation; an
optional LLM draft is used only when it is at least as strict as that reference (INV-11).
"""

from .builder import assemble_contract, build_contract, contract_from_dict, enforce_limits
from .compiler import MODES, apply_intent_limits, build_summary, compile_intent, resolve_mode
from .errors import IntentCompilationError
from .llm import LLMUnavailable, draft_from_json, draft_intent
from .parser import parse_intent
from .preservation import (
    CheckKind,
    PreservationCheck,
    PreservationReport,
    PreservationStatus,
    check_contract_preservation,
    check_preservation,
    parsed_from_contract,
)
from .schema import ParsedCondition, ParsedIntent

__all__ = [
    "MODES",
    "CheckKind",
    "IntentCompilationError",
    "LLMUnavailable",
    "ParsedCondition",
    "ParsedIntent",
    "PreservationCheck",
    "PreservationReport",
    "PreservationStatus",
    "apply_intent_limits",
    "assemble_contract",
    "build_contract",
    "build_summary",
    "check_contract_preservation",
    "check_preservation",
    "compile_intent",
    "contract_from_dict",
    "draft_from_json",
    "draft_intent",
    "enforce_limits",
    "parse_intent",
    "parsed_from_contract",
    "resolve_mode",
]
