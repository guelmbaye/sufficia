"""Intent preservation: a structural check that a draft never grants more authority than the reference.

The deterministic parse of the instruction is the *reference*. A *draft* (an LLM proposal, or a
user-edited contract) is compared with it field by field. The draft may be stricter — a higher lower
bound, a shorter freshness window, an additional requirement — but never more permissive:

* same action type and asset, same currency, amount <= reference amount;
* every reference condition is implied by a draft condition on the same claim
  (``>``/``>=``: draft threshold >= reference; ``<``/``<=``: draft threshold <= reference;
  ``>`` -> ``>=`` at the same threshold is a relaxation);
* effective evidence age <= reference, conflict tolerance <= reference, minimum evidence count
  >= reference, evidence budget <= reference;
* extra draft conditions must be on known claims with allowed operators (they are reported).

This is Document 02 §25 "simple structural comparison" — not a proof of semantic equivalence. It is
deliberately one-directional: anything it cannot show to be at least as strict fails.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..canonical.canonicalize import format_decimal
from ..config import Settings
from ..domain.claims import get_claim
from ..domain.enums import ConflictPolicy, DecisionState, Operator
from ..domain.models import EvidenceContract
from .schema import ParsedCondition, ParsedIntent

LOWER = {Operator.GT, Operator.GTE}
UPPER = {Operator.LT, Operator.LTE}
NUMERIC_OPS = LOWER | UPPER | {Operator.EQ, Operator.NEQ}

_SETTINGS_FIELDS = Settings.model_fields


class PreservationStatus(str, Enum):
    PRESERVED = "PRESERVED"
    RELAXATION_DETECTED = "RELAXATION_DETECTED"
    MISMATCH = "MISMATCH"
    REFERENCE_ONLY = "REFERENCE_ONLY"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class CheckKind(str, Enum):
    RELAXATION = "RELAXATION"  # failing means the draft grants more authority
    MISMATCH = "MISMATCH"  # failing means the draft is not comparable / not representable
    EXTRA = "EXTRA"  # an additional draft constraint (restrictive)
    UNVERIFIED = "UNVERIFIED"  # the reference could not verify this part of the draft

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


class PreservationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: str
    expected: Optional[str] = None
    actual: Optional[str] = None
    ok: bool
    detail: str = ""
    kind: CheckKind = CheckKind.RELAXATION


class PreservationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: PreservationStatus
    checks: list[PreservationCheck] = Field(default_factory=list)
    reference_verified: bool = True  # False when the reference had no conditions to compare with

    @property
    def acceptable(self) -> bool:
        """PRESERVED, or REFERENCE_ONLY (only known restrictive claims; flagged for review)."""
        return self.status in (PreservationStatus.PRESERVED, PreservationStatus.REFERENCE_ONLY)

    @property
    def failed(self) -> list[PreservationCheck]:
        return [c for c in self.checks if not c.ok]

    @property
    def relaxations(self) -> list[PreservationCheck]:
        return [c for c in self.failed if c.kind == CheckKind.RELAXATION]

    def explanation(self) -> str:
        failed = self.failed
        if not failed:
            if self.status == PreservationStatus.REFERENCE_ONLY:
                return "The deterministic reference found no conditions; the draft's conditions could not be verified."
            return "Every reference constraint is preserved."
        return "; ".join(f"{c.check}: {c.detail or 'failed'}" for c in failed)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class PreservationDefaults(BaseModel):
    """Policy defaults used to compare *effective* values when a side leaves a field unset."""

    max_age_seconds: int = _SETTINGS_FIELDS["default_max_age_seconds"].default
    conflict_tolerance: Decimal = _SETTINGS_FIELDS["default_conflict_tolerance"].default
    evidence_budget_usdc: Decimal = _SETTINGS_FIELDS["default_evidence_budget_usdc"].default

    @classmethod
    def from_settings(cls, settings: Settings | None) -> "PreservationDefaults":
        if settings is None:
            return cls()
        return cls(
            max_age_seconds=settings.default_max_age_seconds,
            conflict_tolerance=settings.default_conflict_tolerance,
            evidence_budget_usdc=settings.default_evidence_budget_usdc,
        )


# =============================================================================================
# Formatting
# =============================================================================================


def _num(value: Optional[Decimal]) -> str:
    if value is None:
        return "none"
    try:
        return format_decimal(Decimal(value))
    except (ValueError, ArithmeticError):
        return str(value)


def expression(cond: ParsedCondition) -> str:
    op = cond.operator
    if op in (Operator.EXISTS, Operator.NOT_EXISTS):
        return f"{cond.claim} {op.value}"
    if op == Operator.IN:
        return f"{cond.claim} IN [{', '.join(sorted(cond.values or []))}]"
    if cond.threshold is not None:
        return f"{cond.claim} {op.value} {_num(cond.threshold)}"
    return f"{cond.claim} {op.value} {cond.expected}"


def _secs(value: int) -> str:
    return f"{value} s"


# =============================================================================================
# Implication between two conditions on the same claim
# =============================================================================================


def implies(draft: ParsedCondition, ref: ParsedCondition) -> bool:
    """True when every value satisfying ``draft`` also satisfies ``ref`` (same claim assumed)."""
    ro, do = ref.operator, draft.operator
    if ro == Operator.EXISTS:
        # Any requirement other than NOT_EXISTS needs a current observation of the claim.
        return do != Operator.NOT_EXISTS
    if ro == Operator.NOT_EXISTS:
        return do == Operator.NOT_EXISTS
    if ro == Operator.IN:
        allowed = set(ref.values or [])
        if do == Operator.IN:
            return bool(draft.values) and set(draft.values or []) <= allowed
        if do == Operator.EQ and draft.expected is not None:
            return str(draft.expected) in allowed
        return False
    if ref.threshold is None:  # non-numeric equality
        if do != ro:
            return False
        return draft.expected == ref.expected
    rt, dt = ref.threshold, draft.threshold
    if dt is None or do not in NUMERIC_OPS:
        return False
    if ro == Operator.EQ:
        return do == Operator.EQ and dt == rt
    if ro == Operator.NEQ:
        return {
            Operator.NEQ: dt == rt,
            Operator.EQ: dt != rt,
            Operator.GT: dt >= rt,
            Operator.GTE: dt > rt,
            Operator.LT: dt <= rt,
            Operator.LTE: dt < rt,
        }[do]
    if ro == Operator.GT:
        return (do == Operator.GT and dt >= rt) or (do in (Operator.GTE, Operator.EQ) and dt > rt)
    if ro == Operator.GTE:
        return do in (Operator.GT, Operator.GTE, Operator.EQ) and dt >= rt
    if ro == Operator.LT:
        return (do == Operator.LT and dt <= rt) or (do in (Operator.LTE, Operator.EQ) and dt < rt)
    if ro == Operator.LTE:
        return do in (Operator.LT, Operator.LTE, Operator.EQ) and dt <= rt
    return False  # pragma: no cover


def _why_not(ref: ParsedCondition, candidates: list[ParsedCondition]) -> tuple[str, str]:
    """(actual, detail) explaining why no candidate implies the reference condition."""
    if not candidates:
        return "missing", f"The draft drops the required condition '{expression(ref)}'."
    optional = [c for c in candidates if not c.required]
    if optional and len(optional) == len(candidates):
        return expression(optional[0]) + " (optional)", "The draft makes this required condition optional."
    cands = [c for c in candidates if c.required] or candidates
    actual = " AND ".join(expression(c) for c in cands)
    ro = ref.operator
    same_dir = [c for c in cands if (ro in LOWER and c.operator in LOWER) or (ro in UPPER and c.operator in UPPER)]
    if same_dir and ref.threshold is not None:
        best = same_dir[0]
        dt, rt = best.threshold, ref.threshold
        if dt is not None and dt == rt:
            return actual, (
                f"Operator weakened from '{ro.value}' to '{best.operator.value}' at the same threshold "
                f"(the boundary value {_num(rt)} would now pass)."
            )
        if dt is not None and ro in LOWER and dt < rt:
            return actual, f"Threshold lowered from {_num(rt)} to {_num(dt)}."
        if dt is not None and ro in UPPER and dt > rt:
            return actual, f"Threshold raised from {_num(rt)} to {_num(dt)} on an upper bound."
    if ro in LOWER | UPPER and cands and all(c.operator in LOWER | UPPER for c in cands):
        return actual, f"Operator changed: the reference requires '{ro.value}', the draft does not bound the claim the same way."
    return actual, f"The draft condition does not imply '{expression(ref)}'."


# =============================================================================================
# Checks
# =============================================================================================


def _eff_age(cond: ParsedCondition, intent: ParsedIntent, defaults: PreservationDefaults) -> int:
    stated = [a for a in (cond.max_age_seconds, intent.max_age_seconds) if a is not None]
    return min(stated) if stated else defaults.max_age_seconds


def _eff_tolerance(cond: ParsedCondition, intent: ParsedIntent, defaults: PreservationDefaults) -> Decimal:
    if cond.conflict_tolerance is not None:
        return cond.conflict_tolerance
    if intent.conflict_tolerance is not None:
        return intent.conflict_tolerance
    return defaults.conflict_tolerance


def _eff_conflict_policy(cond: ParsedCondition, intent: ParsedIntent) -> ConflictPolicy:
    return cond.conflict_policy or intent.conflict_policy or ConflictPolicy.ESCALATE


def _condition_problems(cond: ParsedCondition) -> Optional[str]:
    spec = get_claim(cond.claim)
    if spec is None:
        return f"unknown claim '{cond.claim}'"
    if cond.operator not in spec.allowed_operators:
        return f"operator {cond.operator.value} is not allowed for {cond.claim}"
    if cond.operator in (Operator.GT, Operator.GTE, Operator.LT, Operator.LTE) or (
        cond.operator in (Operator.EQ, Operator.NEQ) and spec.numeric
    ):
        if cond.threshold is None or not Decimal(cond.threshold).is_finite():
            return f"a finite threshold is required for {cond.operator.value}"
    if cond.operator == Operator.IN and not cond.values:
        return "IN requires a non-empty value list"
    return None


def check_preservation(
    reference: ParsedIntent,
    draft: ParsedIntent,
    *,
    defaults: PreservationDefaults | Settings | None = None,
) -> PreservationReport:
    """Compares a draft with the deterministic reference. Pure and deterministic."""
    d = defaults if isinstance(defaults, PreservationDefaults) else PreservationDefaults.from_settings(defaults)
    checks: list[PreservationCheck] = []

    def add(check: str, expected: Any, actual: Any, ok: bool, detail: str = "", kind: CheckKind = CheckKind.RELAXATION):
        checks.append(
            PreservationCheck(
                check=check,
                expected=None if expected is None else str(expected),
                actual=None if actual is None else str(actual),
                ok=bool(ok),
                detail=detail,
                kind=kind,
            )
        )

    # ---- action / asset / amount -----------------------------------------------------------------
    ra, da = reference.action, draft.action
    unverifiable = "The deterministic reference could not determine the {0}; the draft's {0} cannot be verified."
    if ra is None:
        add("action", None, da and da.value, False, unverifiable.format("action"), CheckKind.MISMATCH)
    elif da is None:
        add("action", ra and ra.value, None, False, "The draft has no action.", CheckKind.MISMATCH)
    else:
        same = ra == da
        add(
            "action",
            ra and ra.value,
            da.value,
            same,
            "" if same else f"Action changed from {ra and ra.value} to {da.value}.",
        )
    r_asset = (reference.asset or "").strip().upper() or None
    d_asset = (draft.asset or "").strip().upper() or None
    if r_asset is None:
        add("asset", None, d_asset, False, unverifiable.format("asset"), CheckKind.MISMATCH)
    elif d_asset is None:
        add("asset", r_asset, None, False, "The draft has no asset.", CheckKind.MISMATCH)
    else:
        same = r_asset == d_asset
        add("asset", r_asset, d_asset, same, "" if same else f"Asset changed from {r_asset} to {d_asset}.")
    same_ccy = reference.currency == draft.currency
    add(
        "currency",
        reference.currency,
        draft.currency,
        same_ccy,
        "" if same_ccy else "Currencies differ; the amounts cannot be compared.",
        CheckKind.MISMATCH,
    )
    if reference.max_amount is None:
        add("max_amount", None, draft.max_amount is not None and _num(draft.max_amount) or None, False,
            unverifiable.format("maximum amount"), CheckKind.MISMATCH)
    elif draft.max_amount is None:
        add("max_amount", _num(reference.max_amount), None, False, "The draft has no maximum amount.", CheckKind.MISMATCH)
    elif reference.max_amount is not None and same_ccy:
        ok = draft.max_amount <= reference.max_amount
        add(
            "max_amount",
            f"<= {_num(reference.max_amount)} {reference.currency}",
            f"{_num(draft.max_amount)} {draft.currency}",
            ok,
            "" if ok else f"Maximum amount increased from {_num(reference.max_amount)} to {_num(draft.max_amount)}.",
        )

    # ---- global policy ------------------------------------------------------------------------------
    if reference.max_age_seconds is not None or draft.max_age_seconds is not None:
        r_age = reference.max_age_seconds if reference.max_age_seconds is not None else d.max_age_seconds
        d_age = draft.max_age_seconds if draft.max_age_seconds is not None else d.max_age_seconds
        ok = d_age <= r_age
        add(
            "max_age_seconds",
            f"<= {_secs(r_age)}",
            _secs(d_age),
            ok,
            "" if ok else f"Maximum evidence age increased from {r_age} s to {d_age} s.",
        )
    if reference.minimum_required_evidence is not None:
        dm = draft.minimum_required_evidence
        ok = dm is not None and dm >= reference.minimum_required_evidence
        add(
            "minimum_required_evidence",
            f">= {reference.minimum_required_evidence}",
            dm,
            ok,
            "" if ok else f"Minimum evidence count lowered from {reference.minimum_required_evidence} to {dm}.",
        )
    if reference.conflict_policy is not None or draft.conflict_policy is not None:
        rp = reference.conflict_policy or ConflictPolicy.ESCALATE
        dp = draft.conflict_policy or ConflictPolicy.ESCALATE
        # ABSTAIN never executes; ESCALATE opens a human-review path, so ABSTAIN -> ESCALATE relaxes.
        ok = not (rp == ConflictPolicy.ABSTAIN and dp != ConflictPolicy.ABSTAIN)
        add(
            "conflict_policy",
            rp.value,
            dp.value,
            ok,
            "" if ok else "Conflict policy changed from ABSTAIN to ESCALATE (opens a human-review path).",
        )
    if reference.conflict_tolerance is not None or draft.conflict_tolerance is not None:
        rt = reference.conflict_tolerance if reference.conflict_tolerance is not None else d.conflict_tolerance
        dt = draft.conflict_tolerance if draft.conflict_tolerance is not None else d.conflict_tolerance
        ok = dt <= rt
        add(
            "conflict_tolerance",
            f"<= {_num(rt)}",
            _num(dt),
            ok,
            "" if ok else f"Conflict tolerance increased from {_num(rt)} to {_num(dt)}.",
        )
    if reference.evidence_budget_usdc is not None or draft.evidence_budget_usdc is not None:
        rb = reference.evidence_budget_usdc if reference.evidence_budget_usdc is not None else d.evidence_budget_usdc
        db = draft.evidence_budget_usdc if draft.evidence_budget_usdc is not None else d.evidence_budget_usdc
        ok = db <= rb
        add(
            "evidence_budget",
            f"<= {_num(rb)} USDC",
            f"{_num(db)} USDC",
            ok,
            "" if ok else f"Evidence budget increased from {_num(rb)} to {_num(db)} USDC.",
        )

    # ---- draft conditions must be representable ---------------------------------------------------------
    for cond in draft.conditions:
        problem = _condition_problems(cond)
        if problem:
            add(f"draft_condition:{expression(cond)}", "known claim and allowed operator", problem, False,
                f"Draft condition not representable: {problem}.", CheckKind.MISMATCH)

    # ---- every reference condition must be implied ------------------------------------------------------
    used: set[int] = set()
    for ref in reference.conditions:
        label = f"condition:{expression(ref)}"
        candidates = [(i, c) for i, c in enumerate(draft.conditions) if c.claim == ref.claim]
        implying = [(i, c) for i, c in candidates if c.required and implies(c, ref)]
        r_age = _eff_age(ref, reference, d)
        if not implying:
            actual, detail = _why_not(ref, [c for _, c in candidates])
            add(label, expression(ref), actual, False, detail)
            continue
        # prefer an implying condition that also keeps the freshness window
        aged = [(i, c) for i, c in implying if _eff_age(c, draft, d) <= r_age]
        i, match = (aged or implying)[0]
        used.add(i)
        add(label, expression(ref), expression(match), True,
            "" if expression(match) == expression(ref) else "Draft condition is at least as strict.")
        m_age = _eff_age(match, draft, d)
        add(
            f"max_age:{ref.claim}",
            f"<= {_secs(r_age)}",
            _secs(m_age),
            m_age <= r_age,
            "" if m_age <= r_age else f"Maximum evidence age for {ref.claim} increased from {r_age} s to {m_age} s.",
        )
        r_tol, m_tol = _eff_tolerance(ref, reference, d), _eff_tolerance(match, draft, d)
        if m_tol != r_tol or ref.conflict_tolerance is not None:
            add(f"conflict_tolerance:{ref.claim}", f"<= {_num(r_tol)}", _num(m_tol), m_tol <= r_tol,
                "" if m_tol <= r_tol else f"Conflict tolerance for {ref.claim} increased from {_num(r_tol)} to {_num(m_tol)}.")
        r_items, m_items = ref.minimum_items or 1, match.minimum_items or 1
        if r_items != m_items or ref.minimum_items is not None:
            add(f"minimum_items:{ref.claim}", f">= {r_items}", m_items, m_items >= r_items,
                "" if m_items >= r_items else f"Minimum observations for {ref.claim} lowered from {r_items} to {m_items}.")
        r_cp, m_cp = _eff_conflict_policy(ref, reference), _eff_conflict_policy(match, draft)
        if ref.conflict_policy is not None or match.conflict_policy is not None:
            ok = not (r_cp == ConflictPolicy.ABSTAIN and m_cp != ConflictPolicy.ABSTAIN)
            add(f"conflict_policy:{ref.claim}", r_cp.value, m_cp.value, ok,
                "" if ok else f"Conflict policy for {ref.claim} changed from ABSTAIN to ESCALATE.")

    # ---- extra draft conditions (allowed when restrictive and on known claims) ----------------------------
    extras = [c for i, c in enumerate(draft.conditions) if i not in used]
    for cond in extras:
        if _condition_problems(cond):
            continue  # already reported as MISMATCH
        if not cond.required:
            add(f"extra_condition:{expression(cond)}", None, expression(cond) + " (optional)", True,
                "Optional draft condition: it has no effect on authority.", CheckKind.EXTRA)
            continue
        if cond.origin == "IMPLICIT":
            detail = "Implicit requirement added by the compiler (restrictive: it can only remove authority)."
        else:
            detail = "Additional restrictive condition not present in the deterministic reference."
        add(f"extra_condition:{expression(cond)}", None, expression(cond), True, detail, CheckKind.EXTRA)

    reference_user_conditions = [c for c in reference.conditions if c.origin != "IMPLICIT"]
    reference_only = not reference_user_conditions and any(
        c.required and c.origin != "IMPLICIT" for c in extras if not _condition_problems(c)
    )
    if reference_only:
        add(
            "reference_verification",
            "conditions recognised by the deterministic reference",
            "none",
            True,
            "The deterministic reference found no conditions, so the draft's conditions could not be verified "
            "against it. Review them before evidence acquisition.",
            CheckKind.UNVERIFIED,
        )

    failed = [c for c in checks if not c.ok]
    if any(c.kind == CheckKind.RELAXATION for c in failed):
        status = PreservationStatus.RELAXATION_DETECTED
    elif failed:
        status = PreservationStatus.MISMATCH
    elif reference_only:
        status = PreservationStatus.REFERENCE_ONLY
    else:
        status = PreservationStatus.PRESERVED
    return PreservationReport(status=status, checks=checks, reference_verified=not reference_only)


# =============================================================================================
# Contract-level comparison (user-edited contracts)
# =============================================================================================


def parsed_from_contract(contract: EvidenceContract) -> ParsedIntent:
    """Structural view of a contract, with every per-requirement policy value resolved."""
    return ParsedIntent(
        text="",
        source="contract",
        action=contract.action.type,
        asset=contract.action.asset,
        max_amount=contract.action.max_amount.value,
        currency=contract.action.max_amount.currency,
        conditions=[
            ParsedCondition(
                claim=r.claim,
                operator=r.operator,
                threshold=r.threshold,
                expected=r.expected,
                values=r.values,
                max_age_seconds=r.max_age_seconds,
                origin=r.origin,
                required=r.required,
                conflict_tolerance=r.conflict_tolerance,
                minimum_items=r.minimum_items,
                conflict_policy=r.conflict_policy,
            )
            for r in sorted(contract.requirements, key=lambda r: r.id)
        ],
        minimum_required_evidence=contract.policy.minimum_required_evidence,
        evidence_budget_usdc=contract.economic_limits.max_evidence_cost_usd,
    )


_FAILURE_POLICIES = ("missing_required", "stale_required", "critical_conflict", "failed_threshold")


def check_contract_preservation(reference: EvidenceContract, candidate: EvidenceContract) -> PreservationReport:
    """Compares a candidate contract (user-edited, or built from an LLM draft) with the deterministic
    reference contract: every requirement (IMPLICIT ones included) with its resolved per-requirement
    policy, the global policy, economic limits and TTL. Requirement origins decide whether the
    reference had user conditions (REFERENCE_ONLY otherwise)."""
    report = check_preservation(parsed_from_contract(reference), parsed_from_contract(candidate))
    checks = list(report.checks)

    def add(check: str, expected: Any, actual: Any, ok: bool, detail: str, kind: CheckKind = CheckKind.RELAXATION):
        checks.append(
            PreservationCheck(check=check, expected=str(expected), actual=str(actual), ok=ok, detail=detail, kind=kind)
        )

    rt, ct = reference.authorization.ttl_seconds, candidate.authorization.ttl_seconds
    add("authorization_ttl", f"<= {rt} s", f"{ct} s", ct <= rt, "" if ct <= rt else f"Authorization TTL extended from {rt} s to {ct} s.")
    rs, cs = reference.policy.clock_skew_seconds, candidate.policy.clock_skew_seconds
    add("clock_skew_seconds", f"<= {rs} s", f"{cs} s", cs <= rs, "" if cs <= rs else f"Allowed clock skew increased from {rs} s to {cs} s.")
    rv, cv = reference.economic_limits.max_execution_value_usd, candidate.economic_limits.max_execution_value_usd
    add(
        "max_execution_value_usd",
        f"<= {_num(rv)}",
        _num(cv),
        cv <= rv,
        "" if cv <= rv else f"Maximum execution value increased from {_num(rv)} to {_num(cv)}.",
    )
    for name in _FAILURE_POLICIES:
        r_state: DecisionState = getattr(reference.policy, name)
        c_state: DecisionState = getattr(candidate.policy, name)
        if r_state == c_state:
            continue
        if c_state in (DecisionState.AUTHORIZE, DecisionState.ESCALATE):
            add(f"policy.{name}", r_state.value, c_state.value, False,
                f"Failure policy changed to {c_state.value} (opens a path to authority).")
        else:
            add(f"policy.{name}", r_state.value, c_state.value, False,
                "Failure policy changed; only the compiled failure policies are accepted.", CheckKind.MISMATCH)
    for field in ("schema_version", "policy_version"):
        rv_, cv_ = getattr(reference, field), getattr(candidate, field)
        if rv_ != cv_:
            add(field, rv_, cv_, False, f"{field} differs from the compiler's.", CheckKind.MISMATCH)

    failed = [c for c in checks if not c.ok]
    if any(c.kind == CheckKind.RELAXATION for c in failed):
        status = PreservationStatus.RELAXATION_DETECTED
    elif failed:
        status = PreservationStatus.MISMATCH
    else:
        status = report.status
    return PreservationReport(status=status, checks=checks, reference_verified=report.reference_verified)


__all__ = [
    "CheckKind",
    "PreservationCheck",
    "PreservationDefaults",
    "PreservationReport",
    "PreservationStatus",
    "check_contract_preservation",
    "check_preservation",
    "expression",
    "implies",
    "parsed_from_contract",
]
