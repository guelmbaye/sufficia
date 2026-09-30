"""Human-readable explanations derived from structured results (Invariant A3 / INV-14).

Nothing here decides anything: every sentence is a template over a ReasonCode and the exact
structured values that produced it.
"""

from __future__ import annotations

from decimal import ROUND_UP, Decimal
from typing import Any

from ..canonical.canonicalize import format_decimal
from ..domain.claims import get_claim
from ..domain.enums import DecisionState, Operator
from ..domain.models import Requirement
from ..domain.reasons import ReasonCode

OPERATOR_PHRASES = {
    Operator.GT: "more than",
    Operator.GTE: "at least",
    Operator.LT: "less than",
    Operator.LTE: "at most",
    Operator.EQ: "exactly",
    Operator.NEQ: "anything other than",
}

CAUSE_PHRASES = {
    ReasonCode.SOURCE_UNAVAILABLE: "the source was unavailable",
    ReasonCode.PAYMENT_FAILED: "the evidence payment failed",
    ReasonCode.RATE_LIMITED: "the source rate-limited the request",
    ReasonCode.INVALID_RESPONSE: "the source returned an invalid response",
    ReasonCode.MISSING_FIELD: "the source response lacked the required field",
    ReasonCode.BUDGET_EXCEEDED: "it would exceed the evidence budget",
    ReasonCode.SOURCE_NOT_CONFIGURED: "no source is configured for this claim",
    ReasonCode.INVALID_EVIDENCE_TYPE: "the observed value has an invalid type",
    ReasonCode.INVALID_VALUE: "the observed value is not a valid finite number in range",
    ReasonCode.INVALID_UNIT: "the observation used an unexpected unit",
    ReasonCode.INVALID_TIMESTAMP: "the observation has no valid timestamp",
    ReasonCode.FUTURE_TIMESTAMP: "the observation is timestamped in the future",
    ReasonCode.PROVENANCE_MISSING: "the observation lacks provenance",
    ReasonCode.CLAIM_MISMATCH: "the observation addresses a different claim",
    ReasonCode.UNKNOWN_REQUIREMENT: "the observation references an unknown requirement",
    ReasonCode.CONTRACT_MISMATCH: "the observation belongs to another Evidence Contract",
    ReasonCode.SOURCE_NOT_PERMITTED: "the provider is not registered for this claim",
}

HEADLINES = {
    DecisionState.AUTHORIZE: "Execution authorized",
    DecisionState.DENY: "Execution denied",
    DecisionState.ABSTAIN: "Agent abstained",
    DecisionState.REFRESH: "Evidence expired",
    DecisionState.ESCALATE: "Human review required",
}


def label_for(requirement: Requirement) -> str:
    if requirement.label:
        return requirement.label
    spec = get_claim(requirement.claim)
    return spec.label if spec else requirement.claim


def format_value(value: Any, unit: str | None) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        d = Decimal(value)
        if not d.is_finite():
            return str(d)
        if unit in ("USD", "USDC"):
            # Exact digits, never rounded: a failing 499,999.999 must not read as "$500,000.00".
            sign = "-" if d < 0 else ""
            whole, _, frac = format_decimal(abs(d)).partition(".")
            text = f"{int(whole):,}" + (f".{frac}" if frac else "")
            return f"{sign}${text}"
        return format_decimal(d)
    return str(value)


def format_money(value: Decimal, currency: str) -> str:
    return f"{format_decimal(Decimal(value))} {currency}"


def required_expression(requirement: Requirement) -> str:
    op = requirement.operator
    if op in (Operator.EXISTS, Operator.NOT_EXISTS):
        return op.value
    if op == Operator.IN:
        return "IN [" + ", ".join(sorted(requirement.values or [])) + "]"
    if requirement.threshold is not None:
        return f"{op.value} {format_value(requirement.threshold, requirement.unit)}"
    return f"{op.value} {requirement.expected}"


def requirement_phrase(requirement: Requirement) -> str:
    op = requirement.operator
    if op in OPERATOR_PHRASES and requirement.threshold is not None:
        return f"{OPERATOR_PHRASES[op]} {format_value(requirement.threshold, requirement.unit)}"
    if op in OPERATOR_PHRASES:
        return f"{OPERATOR_PHRASES[op]} {requirement.expected}"
    if op == Operator.EXISTS:
        return "a current observation"
    if op == Operator.NOT_EXISTS:
        return "no such observation"
    return "one of " + ", ".join(sorted(requirement.values or []))


def seconds(value: Decimal) -> str:
    """Millisecond display rounded away from zero: 30.0004 s shows as 30.001, never as the 30 s limit."""
    return format_decimal(Decimal(value).quantize(Decimal("0.001"), rounding=ROUND_UP))


def percent(fraction: Any) -> str:
    """Percent display rounded away from zero at 0.001 %: a violation never reads as the tolerance."""
    return format_decimal((Decimal(str(fraction)) * 100).quantize(Decimal("0.001"), rounding=ROUND_UP))


def _num_or_raw(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    try:
        return Decimal(str(value))
    except Exception:
        return value


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text and not text[:2].isupper() else text


def message_for(code: ReasonCode, requirement: Requirement | None, detail: dict[str, Any]) -> str:
    label = label_for(requirement) if requirement else ""
    inner = _lower_first(label)
    if code == ReasonCode.ALL_REQUIREMENTS_SATISFIED:
        return "Every mandatory evidence requirement is satisfied."
    if code == ReasonCode.THRESHOLD_NOT_MET and requirement:
        observed = ", ".join(format_value(v, requirement.unit) for v in detail.get("observed_values", []))
        return f"{label} is {observed}. The Evidence Contract requires {requirement_phrase(requirement)}."
    if code == ReasonCode.PROHIBITED_EVIDENCE_PRESENT and requirement:
        return f"{label} is present, but the Evidence Contract requires that it does not exist."
    if code == ReasonCode.MISSING_REQUIRED_EVIDENCE and requirement:
        causes = [CAUSE_PHRASES[c] for c in detail.get("causes", []) if c in CAUSE_PHRASES]
        tail = f": {'; '.join(causes)}" if causes else ""
        have, need = detail.get("valid_items", 0), requirement.minimum_items
        if need > 1 and have:
            return f"Only {have} valid {inner} observation(s); at least {need} are required{tail}."
        return f"Required {inner} evidence could not be established{tail}."
    if code == ReasonCode.EVIDENCE_BUDGET_EXHAUSTED:
        budget = detail.get("budget")
        spent = detail.get("spent")
        base = f"Acquiring the required evidence would exceed the evidence budget of {format_decimal(Decimal(budget))} USDC"
        if spent is not None:
            base += f" (spent {format_decimal(Decimal(spent))} USDC)"
        if requirement:
            base += f"; {inner} evidence could not be acquired"
        return base + "."
    if code == ReasonCode.INSUFFICIENT_EVIDENCE_COUNT:
        return (
            f"Only {detail.get('valid_items')} valid evidence item(s) are available; "
            f"the Evidence Contract requires at least {detail.get('minimum')}."
        )
    if code == ReasonCode.EVIDENCE_STALE and requirement:
        return (
            f"The {inner} observation is {seconds(Decimal(detail['age_seconds']))} seconds old. "
            f"The Evidence Contract allows a maximum age of {requirement.max_age_seconds} seconds."
        )
    if code == ReasonCode.CRITICAL_SOURCE_CONFLICT and requirement:
        a = format_value(_num_or_raw(detail["value_a"]), requirement.unit)
        b = format_value(_num_or_raw(detail["value_b"]), requirement.unit)
        pct = percent(detail["relative_difference"])
        tol = format_decimal(requirement.conflict_tolerance * 100)
        return (
            f"Two valid sources disagree on {inner}: {a} ({detail['provider_a']}) vs {b} "
            f"({detail['provider_b']}), {pct}% apart; the allowed tolerance is {tol}%."
        )
    if code == ReasonCode.AUTHORITY_WINDOW_EXHAUSTED:
        if requirement and detail.get("age_seconds") is not None:
            return (
                f"Every requirement passes, but the {inner} observation is {seconds(Decimal(detail['age_seconds']))} "
                f"seconds old against a maximum of {requirement.max_age_seconds} seconds: it expires before a "
                "usable authorization window (at least one whole second) can exist."
            )
        return "Every requirement passes, but no usable authorization window (at least one whole second) remains."
    return code.value.replace("_", " ").capitalize() + "."


def consequence(state: DecisionState, amount: str) -> str:
    if state == DecisionState.AUTHORIZE:
        return f"A short-lived authorization bound to this evidence and this exact action may be issued for {amount}."
    if state == DecisionState.REFRESH:
        return f"No execution authority was issued. Refresh the evidence to re-evaluate. {amount} did not move."
    if state == DecisionState.ESCALATE:
        return f"Protected execution is disabled until a human reviews the conflict. {amount} did not move."
    if state == DecisionState.ABSTAIN:
        return f"The system does not know enough to authorize execution. {amount} did not move."
    return f"No execution authorization was issued. {amount} did not move."
