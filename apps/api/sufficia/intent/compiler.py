"""Intent compiler: natural language -> authoritative, validated Evidence Contract.

    text ──► deterministic parse (reference) ──► deterministic contract
       └──► [optional] LLM draft ──► preservation check vs reference ──► LLM contract or FALLBACK
       └──► [optional] user override ──► validation + preservation vs reference ──► accepted?

The LLM may help, but it can never silently relax authority (Document 02 §24-25, Document 03
§11-13, INV-11): an LLM draft is used only when the structural preservation check shows that it is
at least as strict as the deterministic reference; otherwise the deterministic contract is used
and the report says why. A user-edited contract (override) is validated and compared with the
same reference; any relaxation is reported with ``accepted = False`` for the API layer to reject.

The returned :class:`~sufficia.domain.models.CompiledContract` carries:
  * ``contract`` — the Evidence Contract (validated, amount-capped);
  * ``contract_hash`` — :func:`sufficia.canonical.hashes.contract_hash` (ids and labels excluded);
  * ``compiler`` — DETERMINISTIC | LLM | OVERRIDE (what produced ``contract``);
  * ``preservation`` — the preservation report plus compiler provenance (see ``_report_dict``);
  * ``summary`` — the human-readable "What must be true before money can move?" view.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

import httpx
from pydantic import ValidationError

from ..canonical.canonicalize import format_decimal
from ..canonical.hashes import contract_hash
from ..config import Settings
from ..decision_engine.explain import format_money, required_expression
from ..decision_engine.validator import InvalidContractError, validate_contract
from ..domain.claims import get_claim
from ..domain.enums import CompilerKind
from ..domain.models import CompiledContract, EvidenceContract, Intent
from .builder import (
    IMPLICIT,
    IMPLICIT_PRICE_NOTE,
    assemble_contract,
    build_contract,
    check_amount,
    contract_from_dict,
    enforce_limits,
)
from .errors import IntentCompilationError
from .llm import LLMUnavailable, draft_intent
from .parser import parse_intent, plain
from .preservation import (
    CheckKind,
    PreservationCheck,
    PreservationReport,
    PreservationStatus,
    check_contract_preservation,
    check_preservation,
    parsed_from_contract,
)
from .schema import USDC, ParsedIntent

log = logging.getLogger(__name__)

MODES = ("deterministic", "llm", "auto")


# =============================================================================================
# Mode resolution & structured limits
# =============================================================================================


def resolve_mode(mode: Optional[str], settings: Settings) -> str:
    """``deterministic`` | ``llm``; ``auto`` means llm when an Anthropic API key is configured."""
    requested = (mode or settings.intent_compiler or "auto").strip().lower()
    if requested not in MODES:
        raise ValueError(f"unknown intent compiler mode {requested!r}; expected one of {', '.join(MODES)}")
    if requested == "auto":
        return "llm" if settings.anthropic_api_key else "deterministic"
    return requested


def apply_intent_limits(parsed: ParsedIntent, intent: Intent) -> ParsedIntent:
    """Applies the structured limits of the Intent (``protected_amount``, ``evidence_budget``).

    They can only *tighten* what the text says: the maximum amount becomes the smaller of the two
    (or the protected amount when the text has none), the evidence budget likewise. Applied to the
    reference and to any draft before they are compared.
    """
    out = parsed.model_copy(deep=True)
    pa = intent.protected_amount
    if pa is not None:
        currency = USDC if pa.currency.strip().upper() in ("USDC", "USD", "$") else pa.currency.strip().upper()
        value = plain(Decimal(pa.value))
        if out.max_amount is None:
            out.max_amount, out.currency = value, currency
        elif out.currency is None:
            out.currency = currency
            out.max_amount = min(out.max_amount, value)
        elif out.currency != currency:
            raise IntentCompilationError(
                problems=[f"the protected amount is in {currency} but the instruction's maximum is in {out.currency}"],
                code="CURRENCY_MISMATCH",
            )
        elif value < out.max_amount:
            out.warnings.append(
                f"The protected amount ({format_decimal(value)} {currency}) is lower than the amount in the "
                f"instruction ({format_decimal(out.max_amount)} {currency}); the lower one is used."
            )
            out.max_amount = value
    eb = intent.evidence_budget
    if eb is not None and eb.currency.strip().upper() in ("USDC", "USD", "$"):
        budget = plain(Decimal(eb.value))
        out.evidence_budget_usdc = budget if out.evidence_budget_usdc is None else min(out.evidence_budget_usdc, budget)
    return out


# =============================================================================================
# Summary ("What must be true before money can move?")
# =============================================================================================


def _seconds_phrase(values: list[int]) -> str:
    lo, hi = min(values), max(values)
    unit = "second" if hi == 1 else "seconds"
    if lo == hi:
        return f"{hi} {unit}"
    return f"{lo} to {hi} seconds (per requirement)"


def build_summary(
    contract: EvidenceContract,
    *,
    intent_preserved: bool,
    unparsed: list[str],
    warnings: list[str] | None = None,
    compiler: CompilerKind | None = None,
    preservation_status: str | None = None,
) -> dict[str, Any]:
    required = contract.required_requirements
    ages = [r.max_age_seconds for r in contract.requirements] or [contract.authorization.ttl_seconds]
    requirements = []
    for r in contract.requirements:
        spec = get_claim(r.claim)
        item: dict[str, Any] = {
            "id": r.id,
            "label": r.label or (spec.label if spec else r.claim),
            "claim": r.claim,
            "expression": required_expression(r),
            "origin": r.origin,
            "max_age_seconds": r.max_age_seconds,
            "required": r.required,
        }
        if r.origin == IMPLICIT:
            item["note"] = IMPLICIT_PRICE_NOTE if r.claim == "asset_price" else "Added by the compiler (restrictive)."
        requirements.append(item)
    ttl = contract.authorization.ttl_seconds
    bound = min([ttl, *ages])
    summary: dict[str, Any] = {
        "action": f"{contract.action.type.value} {contract.action.asset}",
        "maximum": format_money(contract.action.max_amount.value, contract.action.max_amount.currency),
        "requirements": requirements,
        "time": f"Maximum evidence age {_seconds_phrase(ages)}",
        "authority": f"Authorization expires when supporting evidence expires (max {bound} s)",
        "evidence_budget": format_money(contract.economic_limits.max_evidence_cost_usd, USDC),
        "intent_preserved": bool(intent_preserved),
        "mandatory_requirements": len(required),
        "minimum_required_evidence": contract.policy.minimum_required_evidence,
        "conflict_policy": (required[0].conflict_policy.value if required else None),
        "unparsed": list(unparsed),
        "warnings": list(warnings or []),
    }
    if compiler is not None:
        summary["compiler"] = compiler.value
    if preservation_status is not None:
        summary["preservation_status"] = preservation_status
    return summary


# =============================================================================================
# Report helpers
# =============================================================================================


def _parsed_dict(parsed: ParsedIntent | None) -> dict[str, Any] | None:
    if parsed is None:
        return None
    return parsed.model_dump(mode="json", exclude={"text"})


def _report_dict(
    report: PreservationReport,
    *,
    mode: str,
    compiler: CompilerKind,
    accepted: bool,
    reference: ParsedIntent,
    draft: ParsedIntent | None = None,
    fallback_reason: str | None = None,
    llm_error: LLMUnavailable | None = None,
    draft_report: PreservationReport | None = None,
) -> dict[str, Any]:
    out = report.to_dict()
    out.update(
        {
            "mode": mode,
            "compiler": compiler.value,
            "accepted": bool(accepted),
            "fallback": fallback_reason is not None,
            "fallback_reason": fallback_reason,
            "llm_error": {"code": llm_error.code, "message": str(llm_error)} if llm_error else None,
            "explanation": report.explanation() if fallback_reason is None or llm_error is None else str(llm_error),
            "reference": _parsed_dict(reference),
            "draft": _parsed_dict(draft),
            "reference_unparsed": list(reference.unparsed),
            "reference_warnings": list(reference.warnings),
        }
    )
    if draft_report is not None and draft_report is not report:
        out["draft_status"] = draft_report.status.value
    return out


def _self_check(reference: ParsedIntent, contract: EvidenceContract, settings: Settings) -> PreservationReport:
    """Checks the built contract against the parse it came from (the builder must not relax it)."""
    return check_preservation(reference, parsed_from_contract(contract), defaults=settings)


def _intent_preserved(report: PreservationReport, unparsed: list[str]) -> bool:
    return report.status == PreservationStatus.PRESERVED and not unparsed


def _merge_unparsed(*lists: list[str]) -> list[str]:
    out: list[str] = []
    for lst in lists:
        for item in lst:
            if item not in out:
                out.append(item)
    return out


def _finish(
    contract: EvidenceContract,
    compiler: CompilerKind,
    report_dict: dict[str, Any],
    *,
    report: PreservationReport,
    unparsed: list[str],
    warnings: list[str],
) -> CompiledContract:
    preserved = _intent_preserved(report, unparsed) and report_dict["accepted"]
    report_dict["intent_preserved"] = preserved
    summary = build_summary(
        contract,
        intent_preserved=preserved,
        unparsed=unparsed,
        warnings=warnings,
        compiler=compiler,
        preservation_status=report.status.value,
    )
    return CompiledContract(
        contract=contract,
        contract_hash=contract_hash(contract),
        compiler=compiler,
        preservation=report_dict,
        summary=summary,
    )


# =============================================================================================
# Public API
# =============================================================================================


def compile_intent(
    intent: Intent,
    *,
    contract_id: str,
    settings: Settings,
    mode: str | None = None,
    http_client: httpx.Client | None = None,
    override: dict | None = None,
) -> CompiledContract:
    """Compiles ``intent.text`` into a validated Evidence Contract.

    ``mode``: ``"deterministic"``, ``"llm"`` or ``"auto"`` (default: ``settings.intent_compiler``;
    auto = llm when ``settings.anthropic_api_key`` is set). ``override``: a user-edited contract
    (JSON dict); when given, it is validated and compared with the deterministic reference, and the
    result has ``compiler = OVERRIDE`` and ``preservation["accepted"]`` False on any relaxation.

    Raises :class:`IntentCompilationError` when the instruction cannot be compiled (missing action,
    asset or amount; amount above the protected cap; non-USDC amount; no condition at all for a
    TRANSFER; invalid override) and ``ValueError`` for an unknown mode.
    """
    if not isinstance(intent.text, str) or not intent.text.strip():
        raise IntentCompilationError(missing=["instruction text"], code="EMPTY_INTENT")
    if override is not None:
        if mode is not None and mode.strip().lower() not in (*MODES, "override"):
            raise ValueError(f"unknown intent compiler mode {mode!r}")
        reference = apply_intent_limits(parse_intent(intent.text), intent)
        return _compile_override(intent, override, reference, contract_id=contract_id, settings=settings)
    if mode is not None and mode.strip().lower() == "override":
        raise ValueError("mode 'override' requires the edited contract (override=...)")
    resolved = resolve_mode(mode, settings)
    reference = apply_intent_limits(parse_intent(intent.text), intent)

    # Mandatory parts, currency and amount cap come from the reference: no draft can supply them.
    check_amount(reference, settings)
    det_contract: EvidenceContract | None = None
    det_error: IntentCompilationError | None = None
    try:
        det_contract = build_contract(reference, intent_id=intent.intent_id, contract_id=contract_id, settings=settings)
    except IntentCompilationError as exc:
        if exc.code != "NO_CONDITIONS" or resolved != "llm":
            raise
        det_error = exc  # a TRANSFER with no recognised condition: an LLM draft may still provide one
    except InvalidContractError as exc:  # pragma: no cover - the parser's lexicon is closed
        raise IntentCompilationError(problems=exc.problems, code="INVALID_CONTRACT") from exc

    if resolved == "llm":
        return _compile_llm(
            intent,
            reference,
            det_contract,
            det_error,
            contract_id=contract_id,
            settings=settings,
            http_client=http_client,
        )

    assert det_contract is not None
    report = _self_check(reference, det_contract, settings)
    report_dict = _report_dict(
        report, mode=resolved, compiler=CompilerKind.DETERMINISTIC, accepted=report.acceptable, reference=reference
    )
    return _finish(
        det_contract,
        CompilerKind.DETERMINISTIC,
        report_dict,
        report=report,
        unparsed=reference.unparsed,
        warnings=reference.warnings,
    )


def _compile_llm(
    intent: Intent,
    reference: ParsedIntent,
    det_contract: EvidenceContract | None,
    det_error: IntentCompilationError | None,
    *,
    contract_id: str,
    settings: Settings,
    http_client: httpx.Client | None,
) -> CompiledContract:
    def fallback(
        reason: str,
        report: PreservationReport | None = None,
        *,
        draft: ParsedIntent | None = None,
        llm_error: LLMUnavailable | None = None,
    ) -> CompiledContract:
        if det_contract is None:
            assert det_error is not None
            raise det_error
        self_report = _self_check(reference, det_contract, settings)
        shown = report or self_report
        log.info("intent compiler fell back to the deterministic contract: %s", reason)
        report_dict = _report_dict(
            shown,
            mode="llm",
            compiler=CompilerKind.DETERMINISTIC,
            accepted=self_report.acceptable,
            reference=reference,
            draft=draft,
            fallback_reason=reason,
            llm_error=llm_error,
        )
        # The contract in force is the deterministic one: "intent_preserved" describes *it*, while the
        # report's status/checks explain why the draft was rejected.
        result = _finish(
            det_contract,
            CompilerKind.DETERMINISTIC,
            report_dict,
            report=self_report,
            unparsed=reference.unparsed,
            warnings=reference.warnings,
        )
        result.summary["fallback_reason"] = reason
        return result

    try:
        raw_draft = draft_intent(intent.text, settings, http_client=http_client)
    except LLMUnavailable as exc:
        return fallback(f"LLM_UNAVAILABLE:{exc.code}", llm_error=exc)
    try:
        draft = apply_intent_limits(raw_draft, intent)
    except IntentCompilationError as exc:
        return fallback(f"INVALID_DRAFT:{exc.code}", draft=raw_draft)

    draft_report = check_preservation(reference, draft, defaults=settings)
    if not draft_report.acceptable:
        return fallback(draft_report.status.value, draft_report, draft=draft)

    try:
        llm_contract = build_contract(draft, intent_id=intent.intent_id, contract_id=contract_id, settings=settings)
    except (IntentCompilationError, InvalidContractError) as exc:
        problems = getattr(exc, "problems", None) or [str(exc)]
        failed = PreservationReport(
            status=PreservationStatus.MISMATCH,
            checks=[
                *draft_report.checks,
                PreservationCheck(
                    check="draft_contract",
                    expected="a valid Evidence Contract",
                    actual="invalid",
                    ok=False,
                    detail="The draft could not be built into a valid contract: " + "; ".join(problems),
                    kind=CheckKind.MISMATCH,
                ),
            ],
        )
        return fallback("INVALID_DRAFT_CONTRACT", failed, draft=draft)

    report = draft_report
    if det_contract is not None:
        # Defence in depth: compare the two *contracts* too (implicit requirements, resolved ages,
        # per-requirement policy, TTL, limits).
        contract_report = check_contract_preservation(det_contract, llm_contract)
        if not contract_report.acceptable:
            return fallback(contract_report.status.value, contract_report, draft=draft)
        if draft_report.status == PreservationStatus.PRESERVED and contract_report.status != PreservationStatus.PRESERVED:
            report = contract_report  # pragma: no cover - both views agree for builder-made contracts

    unparsed = _merge_unparsed(draft.unparsed, reference.unparsed)
    report_dict = _report_dict(
        report,
        mode="llm",
        compiler=CompilerKind.LLM,
        accepted=True,
        reference=reference,
        draft=draft,
        draft_report=draft_report,
    )
    return _finish(
        llm_contract,
        CompilerKind.LLM,
        report_dict,
        report=report,
        unparsed=unparsed,
        warnings=_merge_unparsed(reference.warnings, draft.warnings),
    )


def _compile_override(
    intent: Intent,
    override: dict,
    reference: ParsedIntent,
    *,
    contract_id: str,
    settings: Settings,
) -> CompiledContract:
    if not isinstance(override, dict):
        raise IntentCompilationError(problems=["the edited contract must be a JSON object"], code="INVALID_OVERRIDE")
    try:
        contract = contract_from_dict(override, intent_id=intent.intent_id, contract_id=contract_id)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(p) for p in err.get('loc', ())) or 'contract'}: {err.get('msg', 'invalid')}"
            for err in exc.errors()
        ]
        raise IntentCompilationError(problems=problems, code="INVALID_OVERRIDE") from None
    try:
        validate_contract(contract)
    except InvalidContractError as exc:
        raise IntentCompilationError(problems=exc.problems, code="INVALID_OVERRIDE") from exc
    enforce_limits(contract, settings)

    if reference.missing:
        # Nothing to compare the edit with: it cannot be verified, so it cannot be accepted.
        report = check_preservation(reference, parsed_from_contract(contract), defaults=settings)
    else:
        ref_contract = assemble_contract(reference, intent_id=intent.intent_id, contract_id=contract_id, settings=settings)
        report = check_contract_preservation(ref_contract, contract)
    accepted = report.acceptable
    report_dict = _report_dict(
        report, mode="override", compiler=CompilerKind.OVERRIDE, accepted=accepted, reference=reference
    )
    if not accepted:
        report_dict["rejection_reason"] = (
            "The edited contract grants more authority than the instruction: " + report.explanation()
            if report.status == PreservationStatus.RELAXATION_DETECTED
            else "The edited contract could not be verified against the instruction: " + report.explanation()
        )
    return _finish(
        contract,
        CompilerKind.OVERRIDE,
        report_dict,
        report=report,
        unparsed=reference.unparsed,
        warnings=reference.warnings,
    )


__all__ = ["MODES", "apply_intent_limits", "build_summary", "compile_intent", "resolve_mode"]
