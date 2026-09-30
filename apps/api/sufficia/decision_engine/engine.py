"""The deterministic Decision Engine (Document 03 §31-34).

    evaluate(Evidence Contract, Evidence Set, evaluation time, engine version) -> Decision

Pure function: no clock, no network, no database, no model call, no randomness. Same canonical
inputs always produce the same state, reason codes, hashes and fingerprint (INV-13).

Precedence (fixed):
    1. invalid contract                 -> InvalidContractError (no decision)
    2. missing / unaffordable / too few -> policy.missing_required   (ABSTAIN)
    3. stale mandatory evidence         -> policy.stale_required     (REFRESH)
    4. critical source conflict         -> policy.critical_conflict  (ESCALATE)
    5. failed mandatory condition       -> policy.failed_threshold   (DENY)
    6. everything passes                -> AUTHORIZE, unless the supporting evidence leaves no
                                           committable authority window -> policy.stale_required
                                           (AUTHORITY_WINDOW_EXHAUSTED; Document 06 §12)

A failed mandatory condition can never be averaged away: there is no aggregate score.

Arithmetic runs in a private 200-digit Decimal context; evidence values and thresholds are bounded
(magnitude <= 1e18, <= 30 fractional digits), so every subtraction/product is exact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_UP, Decimal, localcontext
from typing import Any, Iterable

from ..canonical.canonicalize import canonical_json, format_datetime, format_decimal
from ..canonical.hashes import (
    contract_hash,
    decision_fingerprint,
    decision_inputs_commitment,
    evidence_item_hash,
    evidence_root,
)
from ..domain.enums import ConflictPolicy, DecisionState, EvidenceStatus, Operator, RequirementStatus
from ..domain.models import (
    AuthorityWindow,
    Decision,
    EvidenceAssessment,
    EvidenceContract,
    EvidenceItem,
    EvidenceSet,
    Reason,
    Requirement,
    RequirementChecks,
    RequirementResult,
)
from ..domain.reasons import ReasonCode
from . import explain
from .rules import Conflict, age_seconds, evidence_expiry, find_conflicts, is_fresh, satisfies
from .validator import validate_contract, validate_item

ENGINE_VERSION = "1.0.0"
SUPPORTED_ENGINE_VERSIONS = frozenset({ENGINE_VERSION})


class UnsupportedEngineVersion(ValueError):
    pass


@dataclass
class _Obs:
    item: EvidenceItem
    value: Any
    observed_at: datetime
    age: Decimal
    fresh: bool
    expires_at: datetime


@dataclass
class _ReqEval:
    requirement: Requirement
    category: str  # pass | fail | missing | stale | conflict
    fresh: list[_Obs] = field(default_factory=list)
    stale: list[_Obs] = field(default_factory=list)
    causes: list[ReasonCode] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    invalid_count: int = 0
    failing_values: list[Any] = field(default_factory=list)


ENGINE_PRECISION = 200


def _q(value: Decimal, places: str = "0.0001") -> Decimal:
    return value.quantize(Decimal(places))


def _q_up(value: Decimal, places: str = "0.001") -> Decimal:
    """Rounds away from zero: a value above a grid boundary is never displayed as the boundary."""
    return value.quantize(Decimal(places), rounding=ROUND_UP)


def _cost(value: Any) -> tuple[Decimal, str]:
    """(counted amount, status) where status is ok | negative | unknown."""
    try:
        d = Decimal(value)
    except Exception:  # pragma: no cover - pydantic already typed it
        return Decimal(0), "unknown"
    if not d.is_finite():
        return Decimal(0), "unknown"
    if d < 0:
        return Decimal(0), "negative"
    return d, "ok"


def _independence(observations: Iterable["_Obs"]) -> tuple[bool, int | None]:
    """Independent source groups: observations sharing a declared family OR an upstream are one source.

    Union-find over both keys, so a provider cannot look independent by declaring a new family
    while relaying the same upstream (e.g. three APIs relaying one oracle count once).
    """
    obs = list(observations)
    if not obs:
        return False, None
    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(k: tuple[str, str]) -> tuple[str, str]:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    roots_of: list[tuple[str, str]] = []
    for o in obs:
        keys = []
        if o.item.source.source_family:
            keys.append(("family", o.item.source.source_family))
        if o.item.source.upstream_source:
            keys.append(("upstream", o.item.source.upstream_source))
        if not keys:
            return False, None
        for k in keys:
            parent.setdefault(k, k)
        for k in keys[1:]:
            ra, rb = find(keys[0]), find(k)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
        roots_of.append(keys[0])
    return True, len({find(k) for k in roots_of})


def evaluate(
    contract: EvidenceContract,
    evidence: EvidenceSet,
    evaluation_time: datetime,
    *,
    engine_version: str = ENGINE_VERSION,
    permitted_sources: dict[str, set[str]] | None = None,
) -> Decision:
    with localcontext() as ctx:
        ctx.prec = ENGINE_PRECISION
        return _evaluate(contract, evidence, evaluation_time, engine_version, permitted_sources)


def _evaluate(
    contract: EvidenceContract,
    evidence: EvidenceSet,
    evaluation_time: datetime,
    engine_version: str,
    permitted_sources: dict[str, set[str]] | None,
) -> Decision:
    if engine_version not in SUPPORTED_ENGINE_VERSIONS:
        raise UnsupportedEngineVersion(engine_version)
    if evaluation_time.tzinfo is None:
        raise ValueError("evaluation_time must be timezone-aware")
    if evidence.contract_id != contract.contract_id:
        raise ValueError("evidence set belongs to another Evidence Contract")
    validate_contract(contract)

    c_hash = contract_hash(contract)
    hashed = [(evidence_item_hash(it), it) for it in evidence.items]
    hashed.sort(key=lambda p: (p[1].evidence_id, p[0]))
    root = evidence_root(h for h, _ in hashed)

    # ---- 1. validate, dedupe and age every item ---------------------------------------------
    assessments: dict[str, EvidenceAssessment] = {}
    order: list[str] = []
    observations: dict[str, list[_Obs]] = {r.id: [] for r in contract.requirements}
    invalid_causes: dict[str, list[ReasonCode]] = {r.id: [] for r in contract.requirements}
    seen_ids: set[str] = set()
    seen_identities: set[str] = set()
    item_costs: dict[str, Decimal] = {}  # keyed by item hash: an identical record is paid for once
    unknown_spend = False

    # One evidence_id carrying different observations is a malformed set: both copies are unusable
    # (never "first one wins", which would depend on hash order).
    hashes_by_id: dict[str, set[str]] = {}
    for h, item in hashed:
        hashes_by_id.setdefault(item.evidence_id, set()).add(h)
    collided = {eid for eid, hs in hashes_by_id.items() if len(hs) > 1}

    for h, item in hashed:
        key = item.evidence_id if item.evidence_id not in assessments else f"{item.evidence_id}#{h[2:10]}"
        order.append(key)
        cost, cost_status = _cost(item.economic.cost)
        item_costs.setdefault(h, cost)
        unknown_spend = unknown_spend or cost_status == "unknown"
        v = validate_item(contract, item, evaluation_time, permitted_sources)
        if item.evidence_id in collided:
            v.codes.append(ReasonCode.EVIDENCE_ID_COLLISION)
        if cost_status != "ok":
            v.codes.append(ReasonCode.INVALID_COST)
        if not v.valid:
            assessments[key] = EvidenceAssessment(
                evidence_id=item.evidence_id,
                requirement_id=item.requirement_id,
                status=EvidenceStatus.INVALID,
                reason_codes=v.codes,
                item_hash=h,
            )
            if item.requirement_id in invalid_causes:
                invalid_causes[item.requirement_id].extend(v.codes)
            continue
        identity = canonical_json(
            [
                item.source.provider,
                item.requirement_id,
                item.claim.name,
                item.integrity.request_hash,
                item.integrity.response_hash,
                v.value,
                v.observed_at,
            ]
        )
        requirement = contract.requirement(item.requirement_id)
        assert requirement is not None and v.observed_at is not None
        if item.evidence_id in seen_ids or identity in seen_identities:
            assessments[key] = EvidenceAssessment(
                evidence_id=item.evidence_id,
                requirement_id=item.requirement_id,
                status=EvidenceStatus.DUPLICATE,
                reason_codes=[ReasonCode.DUPLICATE_EVIDENCE],
                item_hash=h,
            )
            continue
        seen_ids.add(item.evidence_id)
        seen_identities.add(identity)
        age = age_seconds(v.observed_at, evaluation_time)
        fresh = is_fresh(age, requirement.max_age_seconds)
        expires = evidence_expiry(v.observed_at, requirement.max_age_seconds)
        observations[item.requirement_id].append(_Obs(item, v.value, v.observed_at, age, fresh, expires))
        assessments[key] = EvidenceAssessment(
            evidence_id=item.evidence_id,
            requirement_id=item.requirement_id,
            status=EvidenceStatus.VALID if fresh else EvidenceStatus.STALE,
            reason_codes=[] if fresh else [ReasonCode.EVIDENCE_STALE],
            age_seconds=_q_up(age),
            expires_at=expires,
            normalized_value=_fmt(v.value),
            item_hash=h,
        )

    failures_by_req: dict[str, list[ReasonCode]] = {}
    failure_cost = Decimal(0)
    for f in sorted(evidence.acquisition_failures, key=lambda f: (f.requirement_id, f.provider, f.code.value)):
        failures_by_req.setdefault(f.requirement_id, []).append(f.code)
        amount, status = _cost(f.cost_spent)
        failure_cost += amount  # a negative "refund" can never offset real spend
        unknown_spend = unknown_spend or status == "unknown"
    total_cost = sum(item_costs.values(), Decimal(0)) + failure_cost
    budget = contract.economic_limits.max_evidence_cost_usd
    budget_exceeded = total_cost > budget or unknown_spend  # unknown spend fails closed

    # ---- 2. per-requirement evaluation --------------------------------------------------------
    evals: dict[str, _ReqEval] = {}
    for r in contract.requirements:
        obs = observations[r.id]
        fresh = [o for o in obs if o.fresh]
        stale = [o for o in obs if not o.fresh]
        causes = sorted(set(invalid_causes[r.id] + failures_by_req.get(r.id, [])), key=lambda c: c.value)
        ev = _ReqEval(r, "missing", fresh, stale, causes, invalid_count=len(invalid_causes[r.id]))
        if r.operator == Operator.NOT_EXISTS:
            if fresh:
                ev.category = "fail"
                ev.failing_values = [o.value for o in fresh]
            elif stale:
                ev.category = "stale"
            elif causes:
                ev.category = "missing"
            else:
                ev.category = "pass"
        elif len(fresh) >= r.minimum_items:
            ev.conflicts = find_conflicts([(o.item.evidence_id, o.item.source.provider, o.value) for o in fresh], r.conflict_tolerance)
            if ev.conflicts:
                ev.category = "conflict"
            else:
                failing = [o.value for o in fresh if not satisfies(o.value, r)]
                ev.category = "fail" if failing else "pass"
                ev.failing_values = failing
        elif stale and len(fresh) + len(stale) >= r.minimum_items:
            ev.category = "stale"
        else:
            ev.category = "missing"
        evals[r.id] = ev
        for c in ev.conflicts:
            for eid in (c.evidence_a, c.evidence_b):
                a = assessments.get(eid)
                if a is not None and a.status == EvidenceStatus.VALID:
                    a.status = EvidenceStatus.CONFLICTED
                    a.reason_codes = [ReasonCode.CRITICAL_SOURCE_CONFLICT]

    required = [evals[r.id] for r in sorted(contract.requirements, key=lambda r: r.id) if r.required]
    valid_required_items = sum(len(e.fresh) + len(e.stale) for e in required)
    insufficient = valid_required_items < contract.policy.minimum_required_evidence

    missing = [e for e in required if e.category == "missing"]
    stale_reqs = [e for e in required if e.category == "stale"]
    conflict_reqs = [e for e in required if e.category == "conflict"]
    failed = [e for e in required if e.category == "fail"]

    # ---- 3. precedence -------------------------------------------------------------------------
    reasons: list[Reason] = []
    policy = contract.policy
    if missing or budget_exceeded or insufficient:
        state = policy.missing_required
        if budget_exceeded:
            reasons.append(
                _reason(ReasonCode.EVIDENCE_BUDGET_EXHAUSTED, None, {"budget": str(budget), "spent": format_decimal(total_cost)})
            )
        for e in missing:
            code = (
                ReasonCode.EVIDENCE_BUDGET_EXHAUSTED
                if ReasonCode.BUDGET_EXCEEDED in e.causes
                else ReasonCode.MISSING_REQUIRED_EVIDENCE
            )
            detail: dict[str, Any] = {"causes": e.causes, "valid_items": len(e.fresh) + len(e.stale)}
            if code == ReasonCode.EVIDENCE_BUDGET_EXHAUSTED:
                detail["budget"] = str(budget)
            reasons.append(_reason(code, e.requirement, detail, causes=e.causes))
        if insufficient and not missing:
            reasons.append(
                _reason(
                    ReasonCode.INSUFFICIENT_EVIDENCE_COUNT,
                    None,
                    {"valid_items": valid_required_items, "minimum": policy.minimum_required_evidence},
                )
            )
    elif stale_reqs:
        state = policy.stale_required
        for e in stale_reqs:
            freshest = min(e.stale, key=lambda o: o.age)
            reasons.append(
                _reason(
                    ReasonCode.EVIDENCE_STALE,
                    e.requirement,
                    {
                        "age_seconds": format_decimal(_q_up(freshest.age)),
                        "max_age_seconds": e.requirement.max_age_seconds,
                        "evidence_id": freshest.item.evidence_id,
                        "secondary": "THRESHOLD_OBSERVATION_NOT_CURRENT",
                    },
                    observed=[_fmt(o.value) for o in e.stale],
                )
            )
    elif conflict_reqs:
        escalate = any(e.requirement.conflict_policy == ConflictPolicy.ESCALATE for e in conflict_reqs)
        state = policy.critical_conflict if escalate else DecisionState.ABSTAIN
        for e in conflict_reqs:
            c = max(e.conflicts, key=lambda c: (c.relative_difference or Decimal(0), c.evidence_a, c.evidence_b))
            reasons.append(
                _reason(
                    ReasonCode.CRITICAL_SOURCE_CONFLICT,
                    e.requirement,
                    {
                        "evidence_a": c.evidence_a,
                        "evidence_b": c.evidence_b,
                        "provider_a": c.provider_a,
                        "provider_b": c.provider_b,
                        "value_a": _fmt(c.value_a),
                        "value_b": _fmt(c.value_b),
                        "relative_difference": format_decimal(_q_up(c.relative_difference or Decimal(0), "1E-8")),
                        "tolerance": format_decimal(e.requirement.conflict_tolerance),
                        "conflict_count": len(e.conflicts),
                    },
                    observed=[_fmt(o.value) for o in e.fresh],
                )
            )
    elif failed:
        state = policy.failed_threshold
        for e in failed:
            code = (
                ReasonCode.PROHIBITED_EVIDENCE_PRESENT
                if e.requirement.operator == Operator.NOT_EXISTS
                else ReasonCode.THRESHOLD_NOT_MET
            )
            reasons.append(
                _reason(
                    code,
                    e.requirement,
                    {"observed_values": e.failing_values},
                    observed=[_fmt(v) for v in e.failing_values],
                )
            )
    else:
        state = DecisionState.AUTHORIZE
        reasons.append(_reason(ReasonCode.ALL_REQUIREMENTS_SATISFIED, None, {}))

    # ---- 3b. authority window (AUTHORIZE only) ---------------------------------------------------
    # Authority never outlives the supporting evidence (INV-09) and must be committable onchain:
    # EvidenceGate requires expiresAt > issuedAt in whole seconds (Document 06 §12).
    window = None
    if state == DecisionState.AUTHORIZE:
        contract_expiry = evaluation_time + timedelta(seconds=contract.authorization.ttl_seconds)
        bound_by, expires, bounding = "CONTRACT_TTL", contract_expiry, None
        supporting = sorted((o for e in required for o in e.fresh), key=lambda o: (o.expires_at, o.item.evidence_id))
        if supporting and supporting[0].expires_at < expires:
            bounding = supporting[0]
            bound_by, expires = f"EVIDENCE_EXPIRY:{bounding.item.evidence_id}", bounding.expires_at
        if expires <= evaluation_time or math.floor(expires.timestamp()) <= math.floor(evaluation_time.timestamp()):
            state = policy.stale_required
            requirement = contract.requirement(bounding.item.requirement_id) if bounding else None
            reasons = [
                _reason(
                    ReasonCode.AUTHORITY_WINDOW_EXHAUSTED,
                    requirement,
                    {
                        "evidence_id": bounding.item.evidence_id if bounding else None,
                        "age_seconds": format_decimal(_q_up(bounding.age)) if bounding else None,
                        "max_age_seconds": requirement.max_age_seconds if requirement else None,
                        "remaining_seconds": format_decimal(_q(max(Decimal(0), _secs(expires - evaluation_time)), "0.001")),
                    },
                )
            ]
        else:
            window = AuthorityWindow(
                issued_at=evaluation_time,
                expires_at=expires,
                ttl_seconds=_q(_secs(expires - evaluation_time), "0.001"),
                bounded_by=bound_by,
            )

    # ---- 4. requirement results -------------------------------------------------------------
    results = [_requirement_result(evals[r.id]) for r in sorted(contract.requirements, key=lambda r: r.id)]

    # ---- 5. metrics ----------------------------------------------------------------------------
    covered = sum(1 for e in required if len(e.fresh) >= e.requirement.minimum_items or e.category == "pass")
    coverage = _q(Decimal(covered) / Decimal(len(required))) if required else Decimal("1.0000")
    independence_known, independent = _independence(o for e in required for o in e.fresh + e.stale)

    reasons.sort(key=lambda r: (r.requirement_id or "", r.code.value))
    codes: list[ReasonCode] = []
    for r in reasons:
        if r.code not in codes:
            codes.append(r.code)

    evaluated_iso = format_datetime(evaluation_time)
    inputs = decision_inputs_commitment(evidence.acquisition_failures, permitted_sources)
    fp = decision_fingerprint(c_hash, root, evaluated_iso, engine_version, inputs)
    amount = explain.format_money(contract.action.max_amount.value, contract.action.max_amount.currency)
    headline = explain.HEADLINES[state]
    summary = " ".join([r.message for r in reasons] + [explain.consequence(state, amount)])

    return Decision(
        decision_id="dec_" + fp[2:18],
        contract_id=contract.contract_id,
        intent_id=contract.intent_id,
        state=state,
        reason_codes=codes,
        reasons=reasons,
        headline=headline,
        summary=summary,
        requirement_results=results,
        evidence_assessments=[assessments[k] for k in order],
        evidence_ids=sorted({it.evidence_id for _, it in hashed}),
        evidence_count=len({it.evidence_id for _, it in hashed}),
        independent_source_count=independent,
        independence_known=independence_known,
        coverage=coverage,
        freshness_pass=not stale_reqs and ReasonCode.AUTHORITY_WINDOW_EXHAUSTED not in codes,
        conflicts=len(conflict_reqs),
        evidence_cost_usd=total_cost,
        contract_hash=c_hash,
        evidence_root=root,
        engine_version=engine_version,
        evaluated_at=evaluation_time,
        fingerprint=fp,
        authority_window=window,
    )


# ---------------------------------------------------------------------------------------------


def _secs(delta: timedelta) -> Decimal:
    from .rules import exact_seconds

    return exact_seconds(delta)


def _fmt(value: Any) -> str:
    if isinstance(value, Decimal):
        return format_decimal(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _reason(
    code: ReasonCode,
    requirement: Requirement | None,
    detail: dict[str, Any],
    *,
    causes: list[ReasonCode] | None = None,
    observed: list[str] | None = None,
) -> Reason:
    message = explain.message_for(code, requirement, detail)
    clean = {k: ([c.value for c in v] if k == "causes" else (_fmt(v) if isinstance(v, Decimal) else v)) for k, v in detail.items()}
    if "observed_values" in clean:
        clean["observed_values"] = [_fmt(v) for v in detail["observed_values"]]
    return Reason(
        code=code,
        requirement_id=requirement.id if requirement else None,
        claim=requirement.claim if requirement else None,
        observed=observed,
        required=explain.required_expression(requirement) if requirement else None,
        causes=causes or [],
        detail=clean,
        message=message,
    )


def _requirement_result(e: _ReqEval) -> RequirementResult:
    r = e.requirement
    ids = [o.item.evidence_id for o in e.fresh + e.stale]
    values = [_fmt(o.value) for o in (e.fresh or e.stale)]
    checks = RequirementChecks(
        present=bool(e.fresh or e.stale),
        fresh=bool(e.fresh) and e.category != "stale",
        valid_type=e.invalid_count == 0,
        provenance=ReasonCode.PROVENANCE_MISSING not in e.causes,
        conflict=bool(e.conflicts),
        threshold=None if e.category in ("missing", "stale", "conflict") else e.category == "pass",
    )
    if e.category == "pass":
        status, code = RequirementStatus.PASS, None
        human = f"{explain.label_for(r)} satisfies {explain.required_expression(r)}."
    elif e.category == "fail":
        status = RequirementStatus.FAIL
        code = ReasonCode.PROHIBITED_EVIDENCE_PRESENT if r.operator == Operator.NOT_EXISTS else ReasonCode.THRESHOLD_NOT_MET
        human = explain.message_for(code, r, {"observed_values": e.failing_values})
    elif e.category == "stale":
        status, code = RequirementStatus.UNKNOWN, ReasonCode.EVIDENCE_STALE
        freshest = min(e.stale, key=lambda o: o.age)
        human = explain.message_for(code, r, {"age_seconds": freshest.age})
    elif e.category == "conflict":
        status, code = RequirementStatus.UNKNOWN, ReasonCode.CRITICAL_SOURCE_CONFLICT
        c = max(e.conflicts, key=lambda c: (c.relative_difference or Decimal(0), c.evidence_a, c.evidence_b))
        human = explain.message_for(
            code,
            r,
            {
                "value_a": c.value_a,
                "value_b": c.value_b,
                "provider_a": c.provider_a,
                "provider_b": c.provider_b,
                "relative_difference": c.relative_difference or Decimal(0),
            },
        )
    else:
        status = RequirementStatus.UNKNOWN
        code = (
            ReasonCode.EVIDENCE_BUDGET_EXHAUSTED
            if ReasonCode.BUDGET_EXCEEDED in e.causes
            else ReasonCode.MISSING_REQUIRED_EVIDENCE
        )
        human = explain.message_for(
            ReasonCode.MISSING_REQUIRED_EVIDENCE, r, {"causes": e.causes, "valid_items": len(e.fresh) + len(e.stale)}
        )
    return RequirementResult(
        requirement_id=r.id,
        claim=r.claim,
        operator=r.operator,
        required_expression=explain.required_expression(r),
        required=r.required,
        status=status,
        evidence_ids=ids,
        observed_values=values,
        checks=checks,
        reason_code=code,
        human_reason=human,
    )
