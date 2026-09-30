"""Operator semantics, freshness and conflict detection — small, explicit, testable."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import combinations
from typing import Any, Sequence

from ..domain.enums import Operator
from ..domain.models import Requirement

# ---------------------------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------------------------


def exact_seconds(delta: timedelta) -> Decimal:
    """Exact (non-float) number of seconds in a timedelta."""
    return Decimal(delta.days * 86_400 + delta.seconds) + Decimal(delta.microseconds) / Decimal(1_000_000)


def age_seconds(observed_at: datetime, evaluation_time: datetime) -> Decimal:
    """Age at decision time. Observations within the allowed clock skew in the future count as age 0."""
    age = exact_seconds(evaluation_time - observed_at)
    return age if age > 0 else Decimal(0)


def is_fresh(age: Decimal, max_age_seconds: int) -> bool:
    """Fresh when age <= max_age (the boundary itself is fresh; Document 06 §15)."""
    return age <= Decimal(max_age_seconds)


def evidence_expiry(observed_at: datetime, max_age_seconds: int) -> datetime:
    return observed_at + timedelta(seconds=max_age_seconds)


# ---------------------------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------------------------


def satisfies(value: Any, requirement: Requirement) -> bool:
    """Whether one normalized observation satisfies the requirement's operator."""
    op = requirement.operator
    if op == Operator.EXISTS:
        return True
    if op == Operator.IN:
        return str(value) in set(requirement.values or [])
    if isinstance(value, Decimal):
        t = requirement.threshold
        assert t is not None
        return {
            Operator.GT: value > t,
            Operator.GTE: value >= t,
            Operator.LT: value < t,
            Operator.LTE: value <= t,
            Operator.EQ: value == t,
            Operator.NEQ: value != t,
        }[op]
    expected = requirement.expected
    if op == Operator.EQ:
        return value == expected
    if op == Operator.NEQ:
        return value != expected
    raise ValueError(f"operator {op} not applicable")  # pragma: no cover


# ---------------------------------------------------------------------------------------------
# Conflicts (Document 03 §23-26)
# ---------------------------------------------------------------------------------------------


def relative_difference(a: Decimal, b: Decimal) -> Decimal:
    denom = max(abs(a), abs(b))
    if denom == 0:
        return Decimal(0)
    return abs(a - b) / denom


@dataclass(frozen=True)
class Conflict:
    evidence_a: str
    evidence_b: str
    provider_a: str
    provider_b: str
    value_a: Any
    value_b: Any
    relative_difference: Decimal | None


def find_conflicts(observations: Sequence[tuple[str, str, Any]], tolerance: Decimal) -> list[Conflict]:
    """Pairwise material disagreement among fresh valid observations of the same claim.

    A numeric pair conflicts when relative difference is *strictly greater* than the tolerance
    (difference == tolerance is not a conflict). Non-numeric observations conflict when they differ.
    `observations` are (evidence_id, provider, value) sorted by evidence_id.
    """
    conflicts: list[Conflict] = []
    for (id_a, prov_a, va), (id_b, prov_b, vb) in combinations(observations, 2):
        if isinstance(va, Decimal) and isinstance(vb, Decimal):
            # exact test |a-b| > tol * max(|a|,|b|): no division, no rounding at the boundary
            if abs(va - vb) > tolerance * max(abs(va), abs(vb)):
                conflicts.append(Conflict(id_a, id_b, prov_a, prov_b, va, vb, relative_difference(va, vb)))
        elif va != vb:
            conflicts.append(Conflict(id_a, id_b, prov_a, prov_b, va, vb, None))
    return conflicts
