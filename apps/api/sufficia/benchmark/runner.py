"""Canonical benchmark runner (Document 06 §7-25, §54-57, §103, §108).

Loads the committed, human-inspectable benchmark (``tests/benchmark``), evaluates every case with
the deterministic Decision Engine and measures, from the actual engine output (nothing is
hard-coded):

* decision correctness  - produced state == expected state;
* reason fidelity       - exact ordered reason codes, failed requirements and causes;
* explanation fidelity  - every human message is (a) exactly the reason template rendered over
                          the structured detail and (b) consistent with facts recomputed
                          independently from the raw inputs (observed/required values, ages,
                          conflicting pairs, causes, spend, completeness);
* replay fidelity       - canonical snapshot -> reload -> re-evaluate, in this process and in a
                          fresh interpreter process, compares state, reason codes, contract hash,
                          evidence root, fingerprint and the canonical decision hash;
* authority checks      - AUTHORIZE yields a verifiable authorization bound to contract hash,
                          evidence root and action hash and expiring no later than the supporting
                          evidence; every other state is refused by ``build_authorization``.

Fixture numbers are loaded with ``json.loads(..., parse_float=Decimal)``; the canonical
``{"$nonfinite": "NaN"}`` tag is decoded into ``Decimal('NaN')``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_UP, Decimal, InvalidOperation
from itertools import combinations
from pathlib import Path
from typing import Any

from eth_utils import keccak

from ..canonical import canonicalize
from ..canonical.canonicalize import canonical_bytes, canonical_json, format_decimal
from ..canonical.hashes import (
    authorization_id,
    authorization_nonce,
    canonical_action_hash,
    contract_hash,
    evidence_item_hash,
    evidence_root,
    hx,
)
from ..decision_engine import (
    ENGINE_VERSION,
    AuthorityError,
    build_authorization,
    evaluate,
    unix_seconds_floor,
    verify_binding,
)
from ..decision_engine import explain
from ..domain.claims import get_claim
from ..domain.enums import DecisionState, EvidenceStatus, Operator
from ..domain.models import (
    AcquisitionFailure,
    CanonicalAction,
    Decision,
    EvidenceContract,
    EvidenceItem,
    EvidenceSet,
    Requirement,
)
from ..domain.reasons import ReasonCode

PACKAGE_DIR = Path(__file__).resolve().parents[1]  # .../apps/api/sufficia
IMPORT_ROOT = PACKAGE_DIR.parent  # .../apps/api (what must be on sys.path to import sufficia)
REPO_ROOT = IMPORT_ROOT.parents[1]
DEFAULT_ACTION_FIXTURE = "buy_token_x_v1"
MAX_AUTHORIZATION_TTL = 600  # EvidenceGate.MAX_AUTHORIZATION_TTL
FIXTURE_DIRS = ("actions", "contracts", "evidence", "cases")

FROZEN_DISTRIBUTION: dict[str, tuple[str, int]] = {
    "valid": ("AUTHORIZE", 10),
    "missing": ("ABSTAIN", 10),
    "stale": ("REFRESH", 10),
    "threshold": ("DENY", 10),
    "conflict": ("ESCALATE", 5),
    "malformed": ("ABSTAIN", 5),
}
CATEGORY_ORDER = list(FROZEN_DISTRIBUTION)


class BenchmarkError(RuntimeError):
    """The benchmark itself cannot be loaded (missing directory, unreadable fixture...)."""


# =============================================================================================
# Loading
# =============================================================================================


def _object_hook(obj: dict) -> Any:
    if len(obj) == 1 and "$nonfinite" in obj:
        return Decimal(obj["$nonfinite"])
    return obj


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_float=Decimal, object_hook=_object_hook)


def parse_time(value: str | datetime) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise BenchmarkError(f"timestamp must be timezone-aware: {value!r}")
    return dt


def default_benchmark_dir() -> Path:
    env = os.environ.get("SUFFICIA_BENCHMARK_DIR")
    if env:
        return Path(env)
    candidate = REPO_ROOT / "tests" / "benchmark"
    if (candidate / "cases").is_dir():
        return candidate
    for parent in [Path.cwd(), *Path.cwd().parents]:
        if (parent / "tests" / "benchmark" / "cases").is_dir():
            return parent / "tests" / "benchmark"
    return candidate


def default_reports_dir() -> Path:
    env = os.environ.get("SUFFICIA_REPORTS_DIR")
    if env:
        return Path(env)
    return default_benchmark_dir().resolve().parents[1] / "docs" / "evidence"


def resolve_root(path: Path) -> Path:
    path = Path(path)
    if (path / "cases").is_dir():
        return path
    if path.name == "cases" and path.is_dir():
        return path.parent
    raise BenchmarkError(f"no benchmark found at {path} (expected a directory containing cases/)")


def fixture_digest(files: dict[str, bytes]) -> str:
    """Same digest as tests/benchmark/generate_fixtures.py (manifest.json excluded)."""
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode() + b"\x00" + files[rel] + b"\x00")
    return "sha256:" + h.hexdigest()


@dataclass
class Fixtures:
    root: Path
    contracts: dict[str, dict]
    evidence: dict[str, dict]
    actions: dict[str, dict]
    cases: list[dict]
    manifest: dict | None
    digest: str
    integrity_problems: list[str] = field(default_factory=list)

    @property
    def benchmark_version(self) -> str:
        return str((self.manifest or {}).get("benchmark_version", "unversioned"))

    @property
    def is_canonical(self) -> bool:
        return self.manifest is not None


def load_fixtures(path: Path | None = None) -> Fixtures:
    root = resolve_root(path or default_benchmark_dir())
    raw: dict[str, bytes] = {}
    parsed: dict[str, dict[str, Any]] = {d: {} for d in FIXTURE_DIRS}
    for d in FIXTURE_DIRS:
        for p in sorted((root / d).glob("*.json")):
            raw[f"{d}/{p.name}"] = p.read_bytes()
            try:
                parsed[d][p.stem] = load_json(p)
            except (ValueError, OSError) as exc:
                raise BenchmarkError(f"cannot parse {p}: {exc}") from exc
    manifest = load_json(root / "manifest.json") if (root / "manifest.json").exists() else None
    digest = fixture_digest(raw)
    problems: list[str] = []
    if manifest is not None:
        if manifest.get("digest") != digest:
            problems.append(
                f"fixture digest {digest} does not match manifest {manifest.get('digest')}: committed JSON was edited "
                "without re-running tests/benchmark/generate_fixtures.py"
            )
    cases = sorted(parsed["cases"].values(), key=_case_sort_key)
    for stem, case in parsed["cases"].items():
        if case.get("case_id") != stem:
            problems.append(f"cases/{stem}.json declares case_id {case.get('case_id')!r}")
    return Fixtures(root, parsed["contracts"], parsed["evidence"], parsed["actions"], cases, manifest, digest, problems)


def _case_sort_key(case: dict) -> tuple:
    cat = case.get("category", "")
    return (CATEGORY_ORDER.index(cat) if cat in CATEGORY_ORDER else len(CATEGORY_ORDER), cat, case.get("case_id", ""))


# =============================================================================================
# Results
# =============================================================================================


@dataclass
class ReplayResult:
    case_id: str
    snapshot_sha256: str
    snapshot_bytes: int
    original: dict
    in_process: dict | None = None
    cross_process: dict | None = None
    cross_process_required: bool = True
    mismatches: list[str] = field(default_factory=list)

    COMPARED = ("state", "reason_codes", "contract_hash", "evidence_root", "fingerprint")

    def _replays(self) -> list[dict | None]:
        return [self.in_process, self.cross_process] if self.cross_process_required else [self.in_process]

    def field_match(self, name: str) -> bool:
        replays = self._replays()
        return all(r is not None and r.get(name) == self.original.get(name) for r in replays)

    @property
    def matched(self) -> bool:
        return all(self.field_match(f) for f in self.COMPARED)

    @property
    def decision_hash_match(self) -> bool:
        return self.field_match("decision_hash")


@dataclass
class CaseResult:
    case_id: str
    category: str
    description: str
    contract_fixture: str
    evaluation_time: str
    expected: dict
    actual: dict = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)
    explanation_issues: list[str] = field(default_factory=list)
    explanation_advisories: list[str] = field(default_factory=list)
    authority: dict = field(default_factory=dict)
    authority_issues: list[str] = field(default_factory=list)
    authority_advisories: list[str] = field(default_factory=list)
    unauthorized_authorization: bool = False
    replay: ReplayResult | None = None
    error: str | None = None
    decision: Decision | None = None

    @property
    def decision_correct(self) -> bool:
        return self.error is None and self.checks.get("decision", False)

    @property
    def reason_correct(self) -> bool:
        keys = ("reason_codes", "failed_requirements", "causes")
        return self.error is None and all(self.checks.get(k, True) for k in keys) and "reason_codes" in self.checks

    @property
    def expectations_met(self) -> bool:
        return self.error is None and bool(self.checks) and all(self.checks.values())

    @property
    def explanation_faithful(self) -> bool:
        return self.error is None and not self.explanation_issues

    @property
    def replay_matched(self) -> bool:
        return self.replay is not None and self.replay.matched and self.replay.decision_hash_match

    @property
    def passed(self) -> bool:
        return (
            self.expectations_met
            and self.explanation_faithful
            and self.replay_matched
            and not self.authority_issues
            and not self.unauthorized_authorization
        )

    @property
    def advisories(self) -> list[str]:
        return self.explanation_advisories + self.authority_advisories

    def failures(self) -> list[str]:
        out = []
        if self.error:
            out.append(f"error: {self.error}")
        out += self.mismatches
        out += [f"explanation: {m}" for m in self.explanation_issues]
        out += [f"authority: {m}" for m in self.authority_issues]
        if self.unauthorized_authorization:
            out.append("authority: an authorization was issued for a non-AUTHORIZE decision")
        if self.replay is not None:
            out += [f"replay: {m}" for m in self.replay.mismatches]
        elif self.error is None:
            out.append("replay: not performed")
        return out


def _pct(n: int, d: int) -> Decimal:
    if d == 0:
        return Decimal(0)
    return (Decimal(n) * 100 / Decimal(d)).quantize(Decimal("0.01"))


@dataclass
class BenchmarkRun:
    fixtures: Fixtures
    results: list[CaseResult]
    engine_version: str
    cross_process: dict
    generated_at: datetime | None = None
    commit: str = "unversioned"
    strict: bool = False

    @property
    def total(self) -> int:
        return len(self.results)

    def count(self, attr: str) -> int:
        return sum(1 for r in self.results if getattr(r, attr))

    @property
    def metrics(self) -> dict[str, Any]:
        n = self.total
        dec = self.count("decision_correct")
        rsn = self.count("reason_correct")
        exp = self.count("explanation_faithful")
        rep = self.count("replay_matched")
        full = self.count("expectations_met")
        non_auth = [r for r in self.results if r.decision is not None and r.decision.state != DecisionState.AUTHORIZE]
        auth = [r for r in self.results if r.decision is not None and r.decision.state == DecisionState.AUTHORIZE]
        return {
            "total_cases": n,
            "passed_cases": self.count("passed"),
            "decision_correct": dec,
            "decision_correctness_pct": _pct(dec, n),
            "reason_correct": rsn,
            "reason_fidelity_pct": _pct(rsn, n),
            "expectations_met": full,
            "expectation_match_pct": _pct(full, n),
            "explanation_faithful": exp,
            "explanation_fidelity_pct": _pct(exp, n),
            "replay_matched": rep,
            "replay_fidelity_pct": _pct(rep, n),
            "authorizations_issued": sum(1 for r in auth if r.authority.get("authorization_id")),
            "authorizations_verified": sum(1 for r in auth if r.authority.get("authorization_id") and not r.authority_issues),
            "non_authorize_decisions": len(non_auth),
            "non_authorize_refused": sum(1 for r in non_auth if r.authority.get("refused_code") == "EVIDENCE_AUTHORIZATION_REQUIRED"),
            "unauthorized_authorizations": sum(1 for r in self.results if r.unauthorized_authorization),
            "advisories": sum(len(r.advisories) for r in self.results),
        }

    def categories(self) -> list[dict]:
        cats = [c for c in CATEGORY_ORDER if any(r.category == c for r in self.results)]
        cats += sorted({r.category for r in self.results} - set(cats))
        out = []
        for c in cats:
            rs = [r for r in self.results if r.category == c]
            expected_states = sorted({str(r.expected.get("decision")) for r in rs})
            out.append(
                {
                    "category": c,
                    "expected_state": expected_states[0] if len(expected_states) == 1 else expected_states,
                    "frozen_cases": FROZEN_DISTRIBUTION.get(c, (None, None))[1],
                    "cases": len(rs),
                    "decision_correct": sum(r.decision_correct for r in rs),
                    "reason_correct": sum(r.reason_correct for r in rs),
                    "expectations_met": sum(r.expectations_met for r in rs),
                    "explanation_faithful": sum(r.explanation_faithful for r in rs),
                    "replay_matched": sum(r.replay_matched for r in rs),
                    "passed": sum(r.passed for r in rs),
                }
            )
        return out

    def distribution_problems(self) -> list[str]:
        if not self.fixtures.is_canonical:
            return []
        problems = []
        if self.total != 50:
            problems.append(f"the canonical benchmark must contain exactly 50 cases, found {self.total}")
        for cat, (state, n) in FROZEN_DISTRIBUTION.items():
            rs = [r for r in self.results if r.category == cat]
            if len(rs) != n:
                problems.append(f"category {cat}: expected {n} cases, found {len(rs)}")
            bad = [r.case_id for r in rs if r.expected.get("decision") != state]
            if bad:
                problems.append(f"category {cat}: expected state must be {state}: {', '.join(bad)}")
        return problems

    def global_problems(self) -> list[str]:
        problems = list(self.fixtures.integrity_problems) + self.distribution_problems()
        if self.cross_process.get("error"):
            problems.append(f"cross-process replay failed: {self.cross_process['error']}")
        return problems

    @property
    def ok(self) -> bool:
        if self.global_problems() or not self.results:
            return False
        if not all(r.passed for r in self.results):
            return False
        if self.strict and any(r.advisories for r in self.results):
            return False
        return True


# =============================================================================================
# Independent helpers (deliberately NOT the engine's code paths)
# =============================================================================================

OPERATOR_PHRASE = {
    Operator.GT: "more than",
    Operator.GTE: "at least",
    Operator.LT: "less than",
    Operator.LTE: "at most",
    Operator.EQ: "exactly",
    Operator.NEQ: "anything other than",
}
PASSING_STATES = {EvidenceStatus.VALID, EvidenceStatus.CONFLICTED}

# Decision semantics a human must be able to read from the summary (Document 06 §8, §24).
# The first phrase of each state is distinctive and must not appear in any other state's summary.
STATE_SEMANTICS: dict[DecisionState, tuple[str, ...]] = {
    DecisionState.AUTHORIZE: ("authorization bound to this evidence and this exact action may be issued for {amount}",),
    DecisionState.DENY: ("No execution authorization was issued.", "{amount} did not move"),
    DecisionState.ABSTAIN: ("does not know enough to authorize execution", "{amount} did not move"),
    DecisionState.REFRESH: ("Refresh the evidence to re-evaluate", "No execution authority was issued", "{amount} did not move"),
    DecisionState.ESCALATE: ("until a human reviews the conflict", "{amount} did not move"),
}


def _num(value: Any) -> Any:
    """Exact numeric view of a raw observation (bools and strings are not numbers)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    return value


def _plain(d: Decimal) -> str:
    text = format(d, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def display_value(value: Any, unit: str | None) -> str:
    """Documented UI convention (Document 06 §24): ``Liquidity is $92,440``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, Decimal)):
        d = Decimal(value)
        if not d.is_finite():
            return str(d)
        if unit in ("USD", "USDC"):
            sign = "-" if d < 0 else ""
            a = abs(d)
            # exact digits, never rounded (a failing value must never read as the boundary)
            whole, _, frac = _plain(a).partition(".")
            body = f"{int(whole):,}" + (f".{frac}" if frac else "")
            return f"{sign}${body}"
        return _plain(d)
    return str(value)


def _parse_display(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace("$", "").replace(",", ""))
    except InvalidOperation:
        return None


def _label(requirement: Requirement) -> str:
    if requirement.label:
        return requirement.label
    spec = get_claim(requirement.claim)
    return spec.label if spec else requirement.claim


def _age(observed: datetime, evaluation_time: datetime) -> Decimal:
    delta: timedelta = evaluation_time - observed
    secs = Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / Decimal(10**6)
    return max(secs, Decimal(0))


def _satisfies(value: Any, requirement: Requirement) -> bool:
    op = requirement.operator
    if op == Operator.EXISTS:
        return True
    if op == Operator.IN:
        return str(value) in set(requirement.values or [])
    if isinstance(value, Decimal) and requirement.threshold is not None:
        t = Decimal(requirement.threshold)
        return {
            Operator.GT: value > t,
            Operator.GTE: value >= t,
            Operator.LT: value < t,
            Operator.LTE: value <= t,
            Operator.EQ: value == t,
            Operator.NEQ: value != t,
        }[op]
    if op == Operator.EQ:
        return value == requirement.expected
    if op == Operator.NEQ:
        return value != requirement.expected
    return False


def _relative_difference(a: Decimal, b: Decimal) -> Decimal:
    denom = max(abs(a), abs(b))
    return Decimal(0) if denom == 0 else abs(a - b) / denom


@dataclass
class _Facts:
    """Per-requirement facts recomputed from the raw inputs + per-item statuses."""

    requirement: Requirement
    fresh: list[EvidenceItem]
    stale: list[EvidenceItem]
    invalid_codes: list[str]
    failure_codes: list[str]
    conflicts: list[tuple[EvidenceItem, EvidenceItem, Decimal | None]]
    failing: list[Any]
    category: str


def _facts(
    contract: EvidenceContract, evidence_set: EvidenceSet, decision: Decision, evaluation_time: datetime,
    inconsistencies: list[str] | None = None,
) -> dict[str, _Facts]:
    """Per-requirement facts. Item validity (type/unit/provenance...) is taken from the per-item
    assessments; freshness, conflicts and operator outcomes are recomputed here from the raw
    observations, and any assessment whose freshness contradicts the recomputed age is reported."""
    items_by_hash = {evidence_item_hash(it): it for it in evidence_set.items}
    out: dict[str, _Facts] = {}
    for r in contract.requirements:
        fresh, stale, invalid = [], [], []
        for a in decision.evidence_assessments:  # engine order: (evidence_id, item hash)
            it = items_by_hash.get(a.item_hash or "")
            if it is None:
                if inconsistencies is not None:
                    inconsistencies.append(f"assessment {a.evidence_id} does not correspond to any input item")
                continue
            if it.requirement_id != r.id:
                continue
            if a.status in PASSING_STATES or a.status == EvidenceStatus.STALE:
                observed = it.temporal.observed_at
                is_fresh = isinstance(observed, datetime) and _age(observed, evaluation_time) <= r.max_age_seconds
                if inconsistencies is not None and is_fresh != (a.status in PASSING_STATES):
                    inconsistencies.append(f"assessment {a.evidence_id} is {a.status.value} but its recomputed age contradicts it")
            if a.status in PASSING_STATES:
                fresh.append(it)
            elif a.status == EvidenceStatus.STALE:
                stale.append(it)
            elif a.status == EvidenceStatus.INVALID:
                invalid += [c.value for c in a.reason_codes]
        failures = [f.code.value for f in evidence_set.acquisition_failures if f.requirement_id == r.id]
        conflicts: list[tuple[EvidenceItem, EvidenceItem, Decimal | None]] = []
        failing: list[Any] = []
        if r.operator == Operator.NOT_EXISTS:
            category = "fail" if fresh else "stale" if stale else "missing" if (invalid or failures) else "pass"
            failing = [_num(i.claim.value) for i in fresh]
        elif len(fresh) >= r.minimum_items:
            for a_it, b_it in combinations(fresh, 2):
                va, vb = _num(a_it.claim.value), _num(b_it.claim.value)
                if isinstance(va, Decimal) and isinstance(vb, Decimal):
                    diff = _relative_difference(va, vb)
                    if diff > r.conflict_tolerance:
                        conflicts.append((a_it, b_it, diff))
                elif va != vb:
                    conflicts.append((a_it, b_it, None))
            failing = [_num(i.claim.value) for i in fresh if not _satisfies(_num(i.claim.value), r)]
            category = "conflict" if conflicts else "fail" if failing else "pass"
        elif stale and len(fresh) + len(stale) >= r.minimum_items:
            category = "stale"
        else:
            category = "missing"
        out[r.id] = _Facts(r, fresh, stale, sorted(set(invalid)), sorted(set(failures)), conflicts, failing, category)
    return out


def _rehydrate(detail: dict[str, Any]) -> dict[str, Any]:
    d = dict(detail)
    if "causes" in d:
        d["causes"] = [ReasonCode(c) for c in d["causes"]]
    if "observed_values" in d:
        vals = []
        for v in d["observed_values"]:
            try:
                vals.append(Decimal(v) if isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?", v) else v)
            except InvalidOperation:
                vals.append(v)
        d["observed_values"] = vals
    return d


# =============================================================================================
# Explanation fidelity (INV-14, Document 06 §24-25)
# =============================================================================================


@dataclass
class ExplanationCheck:
    issues: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)

    @property
    def faithful(self) -> bool:
        return not self.issues


def check_explanations(
    contract: EvidenceContract, evidence_set: EvidenceSet, evaluation_time: datetime, decision: Decision
) -> ExplanationCheck:
    """Rigorous explanation-fidelity check for one decision.

    Gating issues (``issues``):
      T  template  - message == explain.message_for(code, requirement, structured detail);
      S  semantics - the structured detail and the message agree with facts recomputed from the raw
                     inputs (observed / required values, ages, conflicting pair, causes, spend...);
      C  complete  - the explained requirements are exactly the requirements that drive the state.
    Advisory findings (``advisories``): the *displayed* numbers alone must justify the decision
    (e.g. a REFRESH that says "30 seconds old; maximum age 30 seconds" hides the violation).
    """
    chk = ExplanationCheck()
    issues, adv = chk.issues, chk.advisories
    facts = _facts(contract, evidence_set, decision, evaluation_time, issues)
    mandatory = [r for r in contract.requirements if r.required]
    budget = contract.economic_limits.max_evidence_cost_usd

    # --- decision level ---------------------------------------------------------------------
    if decision.headline != explain.HEADLINES[decision.state]:
        issues.append(f"headline {decision.headline!r} is not the {decision.state.value} headline")
    amount = explain.format_money(contract.action.max_amount.value, contract.action.max_amount.currency)
    expected_summary = " ".join([r.message for r in decision.reasons] + [explain.consequence(decision.state, amount)])
    if decision.summary != expected_summary:
        issues.append("summary is not the concatenation of the reason messages and the state consequence")
    for phrase in STATE_SEMANTICS[decision.state]:
        if phrase.format(amount=amount) not in decision.summary:
            issues.append(f"{decision.state.value} summary does not convey the state semantics ({phrase!r} missing)")
    for other, phrases in STATE_SEMANTICS.items():
        if other != decision.state and phrases[0].format(amount=amount) in decision.summary:
            issues.append(f"{decision.state.value} summary carries {other.value} semantics ({phrases[0]!r})")

    # --- completeness: explained requirements == requirements driving the state ---------------
    explained = sorted({r.requirement_id for r in decision.reasons if r.requirement_id})
    cat_for_state = {
        DecisionState.REFRESH: "stale",
        DecisionState.ESCALATE: "conflict",
        DecisionState.DENY: "fail",
    }
    decisive_cat = cat_for_state.get(decision.state)
    if decision.state == DecisionState.ABSTAIN:
        conflict_abstain = any(r.code == ReasonCode.CRITICAL_SOURCE_CONFLICT for r in decision.reasons)
        decisive_cat = "conflict" if conflict_abstain else "missing"
    if decisive_cat is not None:
        driving = sorted(r.id for r in mandatory if facts[r.id].category == decisive_cat)
        if explained != driving:
            issues.append(f"explained requirements {explained} != requirements that are {decisive_cat} {driving}")
    elif decision.state == DecisionState.AUTHORIZE:
        not_pass = sorted(r.id for r in mandatory if facts[r.id].category != "pass")
        if not_pass or explained:
            issues.append(f"AUTHORIZE while mandatory requirements {not_pass} do not pass")

    # --- per reason ---------------------------------------------------------------------------
    for reason in decision.reasons:
        req = contract.requirement(reason.requirement_id) if reason.requirement_id else None
        where = f"{reason.code.value}[{reason.requirement_id or 'decision'}]"
        msg = reason.message
        # T: template fidelity
        try:
            templated = explain.message_for(reason.code, req, _rehydrate(reason.detail))
        except Exception as exc:  # pragma: no cover - reported, never raised
            templated = f"<template error {type(exc).__name__}: {exc}>"
        if msg != templated:
            issues.append(f"{where}: message is not the template output ({msg!r} != {templated!r})")
        if reason.requirement_id and req is None:
            issues.append(f"{where}: references an unknown requirement")
            continue
        if req is not None:
            if reason.claim != req.claim:
                issues.append(f"{where}: claim {reason.claim!r} != requirement claim {req.claim!r}")
            if reason.required != explain.required_expression(req):
                issues.append(f"{where}: required expression {reason.required!r} is not the contract requirement")
        f = facts.get(req.id) if req else None
        code = reason.code
        if code == ReasonCode.ALL_REQUIREMENTS_SATISFIED:
            if decision.state != DecisionState.AUTHORIZE:
                issues.append(f"{where}: explains a {decision.state.value} decision")
        elif code == ReasonCode.THRESHOLD_NOT_MET and req and f:
            _check_threshold(where, msg, reason.detail, req, f, issues, adv)
        elif code == ReasonCode.PROHIBITED_EVIDENCE_PRESENT and req and f:
            if not f.fresh:
                issues.append(f"{where}: no current observation exists")
            if _label(req) not in msg:
                issues.append(f"{where}: message does not name the claim")
        elif code == ReasonCode.EVIDENCE_STALE and req and f:
            _check_stale(where, msg, reason.detail, req, f, evaluation_time, issues, adv)
        elif code == ReasonCode.CRITICAL_SOURCE_CONFLICT and req and f:
            _check_conflict(where, msg, reason.detail, req, f, issues, adv)
        elif code == ReasonCode.MISSING_REQUIRED_EVIDENCE and req and f:
            _check_missing(where, msg, reason, req, f, issues)
        elif code == ReasonCode.EVIDENCE_BUDGET_EXHAUSTED:
            _check_budget(where, msg, reason, req, f, evidence_set, budget, issues)
        elif code == ReasonCode.INSUFFICIENT_EVIDENCE_COUNT:
            valid = sum(len(facts[r.id].fresh) + len(facts[r.id].stale) for r in mandatory)
            minimum = contract.policy.minimum_required_evidence
            if reason.detail.get("valid_items") != valid or reason.detail.get("minimum") != minimum or valid >= minimum:
                issues.append(f"{where}: detail {reason.detail} != recomputed valid_items={valid}, minimum={minimum}")
            if f"Only {valid} " not in msg or f"at least {minimum}" not in msg:
                issues.append(f"{where}: message does not show the counts {valid}/{minimum}")
        elif req is None and code != ReasonCode.EVIDENCE_BUDGET_EXHAUSTED:
            issues.append(f"{where}: decision-level reason without a semantic check")

    # --- requirement results: FAIL rows must show the failing values --------------------------
    for rr in decision.requirement_results:
        if rr.reason_code == ReasonCode.THRESHOLD_NOT_MET:
            req = contract.requirement(rr.requirement_id)
            for v in facts[rr.requirement_id].failing if req else []:
                if display_value(v, req.unit) not in rr.human_reason:
                    issues.append(f"requirement_result[{rr.requirement_id}]: human reason omits observed value {v}")
    return chk


def _check_threshold(where, msg, detail, req, f: _Facts, issues, adv) -> None:
    try:
        reported = sorted(Decimal(v) for v in detail.get("observed_values", []))
    except (InvalidOperation, TypeError):
        reported = None
    numeric_failing = [v for v in f.failing if isinstance(v, Decimal)]
    if not f.failing:
        issues.append(f"{where}: no current observation actually fails {req.operator.value} {req.threshold}")
    if reported is None or reported != sorted(numeric_failing):
        issues.append(f"{where}: observed_values {detail.get('observed_values')} != failing values {f.failing}")
    if _label(req) not in msg:
        issues.append(f"{where}: message does not name the claim ({_label(req)})")
    for v in f.failing:
        if display_value(v, req.unit) not in msg:
            issues.append(f"{where}: message omits observed value {display_value(v, req.unit)}")
    if req.threshold is not None and req.operator in OPERATOR_PHRASE:
        phrase = f"{OPERATOR_PHRASE[req.operator]} {display_value(req.threshold, req.unit)}"
        if phrase not in msg:
            issues.append(f"{where}: message omits the requirement '{phrase}'")
        shown_t = _parse_display(display_value(req.threshold, req.unit))
        for v in numeric_failing:
            shown_v = _parse_display(display_value(v, req.unit))
            if shown_v is not None and shown_t is not None and _satisfies(shown_v, req.model_copy(update={"threshold": shown_t})):
                adv.append(f"{where}: displayed value {display_value(v, req.unit)} appears to satisfy the displayed requirement")


_AGE_RE = re.compile(r"is (-?\d+(?:\.\d+)?) seconds old")


def _check_stale(where, msg, detail, req, f: _Facts, t: datetime, issues, adv) -> None:
    if not f.stale:
        issues.append(f"{where}: no stale observation exists for {req.id}")
        return
    ages = {i.evidence_id: _age(i.temporal.observed_at, t) for i in f.stale}  # type: ignore[arg-type]
    freshest = min(f.stale, key=lambda i: (ages[i.evidence_id], i.evidence_id))
    eid = detail.get("evidence_id")
    if eid not in ages:
        issues.append(f"{where}: evidence_id {eid!r} is not a stale observation of {req.id}")
        return
    if ages[eid] != ages[freshest.evidence_id]:
        issues.append(f"{where}: reports {eid} but the freshest stale observation is {freshest.evidence_id}")
    age = ages[eid]
    if not age > Decimal(req.max_age_seconds):
        issues.append(f"{where}: reported observation is {age}s old, not older than {req.max_age_seconds}s")
    try:
        detail_age = Decimal(str(detail.get("age_seconds")))
    except InvalidOperation:
        detail_age = None
    if detail_age != age.quantize(Decimal("0.001"), rounding=ROUND_UP):
        issues.append(f"{where}: age_seconds {detail.get('age_seconds')} != recomputed {age.quantize(Decimal('0.001'), rounding=ROUND_UP)}")
    if detail.get("max_age_seconds") != req.max_age_seconds:
        issues.append(f"{where}: max_age_seconds {detail.get('max_age_seconds')} != {req.max_age_seconds}")
    if _label(req).lower() not in msg.lower():
        issues.append(f"{where}: message does not name the claim")
    if f"maximum age of {req.max_age_seconds} seconds" not in msg:
        issues.append(f"{where}: message omits the maximum age {req.max_age_seconds}s")
    m = _AGE_RE.search(msg)
    if not m:
        issues.append(f"{where}: message does not state the observation age")
        return
    shown = Decimal(m.group(1))
    if abs(shown - age) > Decimal("0.5"):
        issues.append(f"{where}: message states age {shown}s but the observation is {age}s old")
    if not shown > Decimal(req.max_age_seconds):
        adv.append(
            f"{where}: message says the observation is {m.group(1)} seconds old with a maximum age of "
            f"{req.max_age_seconds} seconds; the display hides the violation (actual age {age}s)"
        )


_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)% apart; the allowed tolerance is (\d+(?:\.\d+)?)%")


def _check_conflict(where, msg, detail, req, f: _Facts, issues, adv) -> None:
    if not f.conflicts:
        issues.append(f"{where}: no pair of current observations differs by more than {req.conflict_tolerance}")
        return
    worst = max(f.conflicts, key=lambda c: (c[2] or Decimal(0), c[0].evidence_id, c[1].evidence_id))
    a, b, diff = worst
    expected = {
        "evidence_a": a.evidence_id,
        "evidence_b": b.evidence_id,
        "provider_a": a.source.provider,
        "provider_b": b.source.provider,
        "conflict_count": len(f.conflicts),
        "tolerance": format_decimal(req.conflict_tolerance),
    }
    for k, v in expected.items():
        if detail.get(k) != v:
            issues.append(f"{where}: detail {k}={detail.get(k)!r} != recomputed {v!r}")
    for k, it in (("value_a", a), ("value_b", b)):
        if _parse_display(str(detail.get(k))) != _num(it.claim.value) and str(detail.get(k)) != str(it.claim.value):
            issues.append(f"{where}: detail {k}={detail.get(k)!r} != observed {it.claim.value!r}")
    if diff is not None:
        if _parse_display(str(detail.get("relative_difference"))) != diff.quantize(Decimal("1E-8"), rounding=ROUND_UP):
            issues.append(f"{where}: relative_difference {detail.get('relative_difference')} != recomputed {diff}")
    for token in (display_value(_num(a.claim.value), req.unit), display_value(_num(b.claim.value), req.unit),
                  a.source.provider, b.source.provider):
        if token not in msg:
            issues.append(f"{where}: message omits {token!r}")
    m = _PCT_RE.search(msg)
    if not m:
        issues.append(f"{where}: message does not state the disagreement and the tolerance")
        return
    shown_pct, shown_tol = Decimal(m.group(1)), Decimal(m.group(2))
    if diff is not None and abs(shown_pct - diff * 100) > Decimal("0.05"):
        issues.append(f"{where}: message states {shown_pct}% but the sources differ by {diff * 100}%")
    if shown_tol != req.conflict_tolerance * 100:
        issues.append(f"{where}: message states tolerance {shown_tol}% but the contract allows {req.conflict_tolerance * 100}%")
    if not shown_pct > shown_tol:
        adv.append(f"{where}: displayed disagreement {shown_pct}% does not exceed displayed tolerance {shown_tol}%")


def _check_missing(where, msg, reason, req, f: _Facts, issues) -> None:
    have = len(f.fresh) + len(f.stale)
    if f.category != "missing":
        issues.append(f"{where}: requirement is {f.category}, not missing")
    if reason.detail.get("valid_items") != have:
        issues.append(f"{where}: valid_items {reason.detail.get('valid_items')} != recomputed {have}")
    expected_causes = sorted(set(f.invalid_codes) | set(f.failure_codes))
    actual_causes = [c.value for c in reason.causes]
    if actual_causes != expected_causes:
        issues.append(f"{where}: causes {actual_causes} != recomputed {expected_causes}")
    if ReasonCode.BUDGET_EXCEEDED.value in expected_causes:
        issues.append(f"{where}: a budget failure must be explained as EVIDENCE_BUDGET_EXHAUSTED")
    if _label(req).lower() not in msg.lower():
        issues.append(f"{where}: message does not name the claim")
    for c in reason.causes:
        phrase = explain.CAUSE_PHRASES.get(c)
        if phrase is None or phrase not in msg:
            issues.append(f"{where}: cause {c.value} is not explained in the message")
    if req.minimum_items > 1 and have and f"at least {req.minimum_items}" not in msg:
        issues.append(f"{where}: message omits the minimum of {req.minimum_items} observations")


def _check_budget(where, msg, reason, req, f, evidence_set: EvidenceSet, budget: Decimal, issues) -> None:
    if format_decimal(Decimal(str(reason.detail.get("budget")))) != format_decimal(budget):
        issues.append(f"{where}: budget {reason.detail.get('budget')} != contract budget {budget}")
    if f"evidence budget of {format_decimal(budget)} USDC" not in msg:
        issues.append(f"{where}: message omits the budget")
    if req is None:
        costs: dict[str, Decimal] = {}
        for it in sorted(evidence_set.items, key=lambda i: (i.evidence_id, evidence_item_hash(i))):
            costs.setdefault(it.evidence_id, Decimal(it.economic.cost))
        spent = sum(costs.values(), Decimal(0)) + sum((Decimal(x.cost_spent) for x in evidence_set.acquisition_failures), Decimal(0))
        if not spent > budget:
            issues.append(f"{where}: recomputed spend {spent} does not exceed the budget {budget}")
        if _parse_display(str(reason.detail.get("spent"))) != spent:
            issues.append(f"{where}: spent {reason.detail.get('spent')} != recomputed {spent}")
        if f"spent {format_decimal(spent)} USDC" not in msg:
            issues.append(f"{where}: message omits the amount spent")
    else:
        if ReasonCode.BUDGET_EXCEEDED not in reason.causes:
            issues.append(f"{where}: no BUDGET_EXCEEDED acquisition failure for {req.id}")
        if _label(req).lower() not in msg.lower():
            issues.append(f"{where}: message does not name the claim")
        if f is not None and f.category != "missing":
            issues.append(f"{where}: requirement is {f.category}, not missing")


# =============================================================================================
# Authority checks (Document 06 §11-12, INV-01, INV-09)
# =============================================================================================


def _action_for(spec: dict, decision: Decision, deadline: int) -> CanonicalAction:
    body = copy.deepcopy(spec["action"])
    body["nonce"] = authorization_nonce(decision.fingerprint)
    body["deadline"] = deadline
    return CanonicalAction.model_validate(body)


def check_authority(
    contract: EvidenceContract,
    evidence_set: EvidenceSet,
    evaluation_time: datetime,
    decision: Decision,
    action_spec: dict,
) -> tuple[dict, list[str], list[str], bool]:
    """Returns (authority summary, issues, advisories, unauthorized_authorization)."""
    info: dict[str, Any] = {}
    issues: list[str] = []
    adv: list[str] = []
    executor = action_spec["executor"]
    recomputed_root = evidence_root(evidence_item_hash(i) for i in evidence_set.items)
    recomputed_contract = contract_hash(contract)
    if decision.contract_hash != recomputed_contract:
        issues.append("decision contract_hash does not match the recomputed contract hash")
    if decision.evidence_root != recomputed_root:
        issues.append("decision evidence_root does not match the recomputed evidence root")

    if decision.state != DecisionState.AUTHORIZE:
        action = _action_for(action_spec, decision, unix_seconds_floor(evaluation_time) + 30)
        try:
            build_authorization(
                decision=decision, contract=contract, action=action, executor=executor,
                onchain_intent_id=action_spec["onchain_intent_id"],
            )
        except AuthorityError as exc:
            info["refused_code"] = exc.code.value
            if exc.code != ReasonCode.EVIDENCE_AUTHORIZATION_REQUIRED:
                issues.append(f"non-AUTHORIZE decision refused with {exc.code.value}, expected EVIDENCE_AUTHORIZATION_REQUIRED")
            return info, issues, adv, False
        return info, issues, adv, True

    window = decision.authority_window
    assert window is not None
    action = _action_for(action_spec, decision, unix_seconds_floor(window.expires_at))
    try:
        auth = build_authorization(
            decision=decision, contract=contract, action=action, executor=executor,
            onchain_intent_id=action_spec["onchain_intent_id"],
        )
    except AuthorityError as exc:
        issues.append(f"AUTHORIZE decision could not produce an authorization: {exc.code.value}: {exc}")
        return info, issues, adv, False
    a_hash = canonical_action_hash(action)
    info.update(
        {
            "authorization_id": auth.authorization_id,
            "contract_hash": auth.contract_hash,
            "evidence_root": auth.evidence_root,
            "action_hash": auth.action_hash,
            "nonce": auth.nonce,
            "issued_at": auth.issued_at,
            "expires_at": auth.expires_at,
            "bounded_by": window.bounded_by,
        }
    )
    # supporting evidence = valid, fresh observations of mandatory requirements (recomputed from inputs)
    by_hash = {a.item_hash: a for a in decision.evidence_assessments}
    expiries = []
    for it in evidence_set.items:
        req = contract.requirement(it.requirement_id)
        a = by_hash.get(evidence_item_hash(it))
        if req is not None and req.required and a is not None and a.status == EvidenceStatus.VALID:
            expiries.append(it.temporal.observed_at + timedelta(seconds=req.max_age_seconds))  # type: ignore[operator]
    earliest = min(expiries) if expiries else None
    info["earliest_evidence_expiry"] = earliest
    checks = [
        (auth.expires_at > evaluation_time, "expires_at must be later than evaluated_at"),
        (earliest is not None and auth.expires_at <= earliest, "authority outlives the supporting evidence (INV-09)"),
        (auth.expires_at <= evaluation_time + timedelta(seconds=contract.authorization.ttl_seconds), "authority exceeds the contract TTL"),
        (auth.expires_at - evaluation_time <= timedelta(seconds=MAX_AUTHORIZATION_TTL), "authority exceeds MAX_AUTHORIZATION_TTL"),
        (auth.contract_hash == recomputed_contract, "authorization contract_hash mismatch"),
        (auth.evidence_root == recomputed_root, "authorization evidence_root mismatch"),
        (auth.action_hash == a_hash, "authorization action_hash mismatch"),
        (
            auth.authorization_id == authorization_id(recomputed_contract, recomputed_root, a_hash, executor, auth.nonce),
            "authorization_id does not recompute",
        ),
        (auth.nonce == authorization_nonce(decision.fingerprint), "nonce is not bound to the decision fingerprint"),
        (verify_binding(auth, decision, action) == [], f"verify_binding: {verify_binding(auth, decision, action)}"),
    ]
    issues += [msg for ok, msg in checks if not ok]
    issued_ts, expires_ts = unix_seconds_floor(auth.issued_at), unix_seconds_floor(auth.expires_at)
    info["onchain_committable"] = expires_ts > issued_ts
    if expires_ts <= issued_ts:
        adv.append(
            f"authority window {window.ttl_seconds}s truncates to expiresAt ({expires_ts}) <= issuedAt ({issued_ts}) in whole "
            "seconds: EvidenceGate.authorize would revert InvalidAuthorization"
        )
    return info, issues, adv, False


# =============================================================================================
# Replay (Document 06 §54-58)
# =============================================================================================


# Snapshots live in the engine package so the API, the benchmark and the tests share one format.
from ..decision_engine.snapshot import make_snapshot, replay_snapshot  # noqa: E402,F401


def decision_digest(decision: Decision) -> dict:
    return {
        "state": decision.state.value,
        "reason_codes": [c.value for c in decision.reason_codes],
        "contract_hash": decision.contract_hash,
        "evidence_root": decision.evidence_root,
        "fingerprint": decision.fingerprint,
        "decision_id": decision.decision_id,
        "engine_version": decision.engine_version,
        "decision_hash": hx(keccak(canonical_bytes(decision))),
    }


def replay_worker() -> int:
    """Entry point of the cross-process replay: reads {"snapshots": {case_id: text}} on stdin."""
    payload = json.load(sys.stdin)
    results: dict[str, dict] = {}
    for case_id, snapshot in payload["snapshots"].items():
        try:
            results[case_id] = decision_digest(replay_snapshot(snapshot))
        except Exception as exc:  # reported by the parent as a mismatch
            results[case_id] = {"error": f"{type(exc).__name__}: {exc}"}
    json.dump({"pid": os.getpid(), "results": results}, sys.stdout)
    return 0


def cross_process_replay(snapshots: dict[str, str], timeout: float = 600) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(IMPORT_ROOT)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONIOENCODING"] = "utf-8"  # same bytes on every OS (Windows pipes default to the ANSI code page)
    cmd = [sys.executable, "-c", "import sys; from sufficia.benchmark.runner import replay_worker; sys.exit(replay_worker())"]
    try:
        proc = subprocess.run(
            cmd, input=json.dumps({"snapshots": snapshots}), capture_output=True, text=True, encoding="utf-8", env=env,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "results": {}}
    if proc.returncode != 0:
        return {"error": f"exit {proc.returncode}: {proc.stderr.strip()[-2000:]}", "results": {}}
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return {"error": f"unparseable worker output: {proc.stdout[:500]!r}", "results": {}}
    out["parent_pid"] = os.getpid()
    return out


def _compare_replay(res: ReplayResult) -> None:
    pairs = [("in-process", res.in_process)]
    if res.cross_process_required:
        pairs.append(("cross-process", res.cross_process))
    for label, replay in pairs:
        if replay is None:
            res.mismatches.append(f"{label} replay missing")
            continue
        if "error" in replay:
            res.mismatches.append(f"{label} replay raised {replay['error']}")
            continue
        for name in (*ReplayResult.COMPARED, "decision_id", "engine_version", "decision_hash"):
            if replay.get(name) != res.original.get(name):
                res.mismatches.append(f"{label} {name}: {replay.get(name)!r} != original {res.original.get(name)!r}")


# =============================================================================================
# Case execution
# =============================================================================================


def build_inputs(fx: Fixtures, case: dict) -> tuple[EvidenceContract, EvidenceSet, datetime]:
    name = case["contract_fixture"]
    if name not in fx.contracts:
        raise BenchmarkError(f"unknown contract fixture {name!r}")
    contract = EvidenceContract.model_validate(copy.deepcopy(fx.contracts[name]))
    items = []
    for entry in case.get("evidence", []):
        if isinstance(entry, str):
            if entry not in fx.evidence:
                raise BenchmarkError(f"unknown evidence fixture {entry!r}")
            items.append(EvidenceItem.model_validate(copy.deepcopy(fx.evidence[entry])))
        else:
            items.append(EvidenceItem.model_validate(copy.deepcopy(entry)))
    failures = [AcquisitionFailure.model_validate(copy.deepcopy(f)) for f in case.get("acquisition_failures", [])]
    evidence_set = EvidenceSet(contract_id=contract.contract_id, items=items, acquisition_failures=failures)
    return contract, evidence_set, parse_time(case["evaluation_time"])


def _values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool) or expected is None or actual is None:
        return actual == expected
    if isinstance(expected, (int, Decimal)) and not isinstance(expected, bool):
        try:
            return Decimal(str(actual)) == Decimal(expected)
        except InvalidOperation:
            return False
    return actual == expected


def failed_requirements(decision: Decision) -> list[str]:
    """Requirements named by the decisive reasons, in reason order (== sorted by requirement id)."""
    out: list[str] = []
    for r in decision.reasons:
        if r.requirement_id and r.requirement_id not in out:
            out.append(r.requirement_id)
    return out


def _reason_for(decision: Decision, key: str):
    rid = None if key == "_decision" else key
    return [r for r in decision.reasons if r.requirement_id == rid]


def compare_expectations(expected: dict, decision: Decision) -> tuple[dict[str, bool], list[str], dict]:
    checks: dict[str, bool] = {}
    mismatches: list[str] = []
    actual: dict[str, Any] = {
        "decision": decision.state.value,
        "reason_codes": [c.value for c in decision.reason_codes],
        "failed_requirements": failed_requirements(decision),
        "causes": {r.requirement_id: [c.value for c in r.causes] for r in decision.reasons if r.requirement_id},
        "requirement_status": {rr.requirement_id: rr.status.value for rr in decision.requirement_results},
    }
    if decision.authority_window is not None:
        actual["authority_expires_at"] = canonicalize.format_datetime(decision.authority_window.expires_at)
        actual["authority_bounded_by"] = decision.authority_window.bounded_by
        actual["authority_ttl_seconds"] = format_decimal(decision.authority_window.ttl_seconds)

    def check(name: str, ok: bool, detail: str) -> None:
        checks[name] = checks.get(name, True) and ok
        if not ok:
            mismatches.append(f"{name}: {detail}")

    check("decision", actual["decision"] == expected.get("decision"),
          f"expected {expected.get('decision')}, got {actual['decision']}")
    check("reason_codes", actual["reason_codes"] == list(expected.get("reason_codes", [])),
          f"expected {expected.get('reason_codes')}, got {actual['reason_codes']}")
    check("failed_requirements", actual["failed_requirements"] == list(expected.get("failed_requirements", [])),
          f"expected {expected.get('failed_requirements')}, got {actual['failed_requirements']}")
    for req, codes in (expected.get("causes") or {}).items():
        rs = _reason_for(decision, req)
        got = [c.value for c in rs[0].causes] if rs else None
        check("causes", got == list(codes), f"{req}: expected {codes}, got {got}")
    if "authority_expires_at" in expected:
        w = decision.authority_window
        ok = w is not None and w.expires_at == parse_time(expected["authority_expires_at"])
        check("authority_expires_at", ok, f"expected {expected['authority_expires_at']}, got {actual.get('authority_expires_at')}")
    if "authority_bounded_by" in expected:
        got = decision.authority_window.bounded_by if decision.authority_window else None
        check("authority_bounded_by", got == expected["authority_bounded_by"], f"expected {expected['authority_bounded_by']}, got {got}")
    if "authority_ttl_seconds" in expected:
        got = decision.authority_window.ttl_seconds if decision.authority_window else None
        check("authority_ttl_seconds", got is not None and _values_equal(got, expected["authority_ttl_seconds"]),
              f"expected {expected['authority_ttl_seconds']}, got {got}")
    for req, st in (expected.get("requirement_status") or {}).items():
        got = actual["requirement_status"].get(req)
        check("requirement_status", got == st, f"{req}: expected {st}, got {got}")
    for eid, exp in (expected.get("evidence") or {}).items():
        found = [a for a in decision.evidence_assessments if a.evidence_id == eid]
        if not found:
            check("evidence", False, f"{eid}: no assessment")
            continue
        a = found[0]
        if "status" in exp:
            check("evidence", a.status.value == exp["status"], f"{eid}: expected status {exp['status']}, got {a.status.value}")
        if "reason_codes" in exp:
            got = [c.value for c in a.reason_codes]
            check("evidence", got == list(exp["reason_codes"]), f"{eid}: expected codes {exp['reason_codes']}, got {got}")
    for key, subset in (expected.get("reason_detail") or {}).items():
        rs = _reason_for(decision, key)
        if not rs:
            check("reason_detail", False, f"{key}: no reason")
            continue
        for k, v in subset.items():
            got = rs[0].detail.get(k)
            check("reason_detail", _values_equal(got, v), f"{key}.{k}: expected {v!r}, got {got!r}")
    for k, v in (expected.get("metrics") or {}).items():
        got = getattr(decision, k, None)
        check("metrics", _values_equal(got, v), f"{k}: expected {v!r}, got {got!r}")
    for key, text in (expected.get("messages") or {}).items():
        rs = _reason_for(decision, key)
        got = rs[0].message if rs else None
        check("messages", got == text, f"{key}: expected {text!r}, got {got!r}")
    return checks, mismatches, actual


def run_case(fx: Fixtures, case: dict) -> tuple[CaseResult, str | None]:
    res = CaseResult(
        case_id=str(case.get("case_id")),
        category=str(case.get("category")),
        description=str(case.get("description", "")),
        contract_fixture=str(case.get("contract_fixture")),
        evaluation_time=str(case.get("evaluation_time")),
        expected=case.get("expected", {}),
    )
    snapshot = None
    try:
        contract, evidence_set, t = build_inputs(fx, case)
        decision = evaluate(contract, evidence_set, t)
        res.decision = decision
        res.checks, res.mismatches, res.actual = compare_expectations(res.expected, decision)
        expl = check_explanations(contract, evidence_set, t, decision)
        res.explanation_issues, res.explanation_advisories = expl.issues, expl.advisories
        spec_name = case.get("action_fixture", DEFAULT_ACTION_FIXTURE)
        if spec_name not in fx.actions:
            raise BenchmarkError(f"unknown action fixture {spec_name!r}")
        res.authority, res.authority_issues, res.authority_advisories, res.unauthorized_authorization = check_authority(
            contract, evidence_set, t, decision, fx.actions[spec_name]
        )
        snapshot = make_snapshot(contract, evidence_set, t, decision.engine_version)
        res.replay = ReplayResult(
            case_id=res.case_id,
            snapshot_sha256="sha256:" + hashlib.sha256(snapshot.encode("ascii")).hexdigest(),
            snapshot_bytes=len(snapshot),
            original=decision_digest(decision),
        )
        try:
            res.replay.in_process = decision_digest(replay_snapshot(snapshot))
        except Exception as exc:
            res.replay.in_process = {"error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:
        res.error = f"{type(exc).__name__}: {exc}"
    return res, snapshot


def git_commit(root: Path) -> str:
    for var in ("SUFFICIA_COMMIT_SHA", "GITHUB_SHA"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unversioned"
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else "unversioned"


def run_benchmark(cases: Path | None = None, *, strict: bool = False, cross_process: bool = True) -> BenchmarkRun:
    fx = load_fixtures(cases)
    results: list[CaseResult] = []
    snapshots: dict[str, str] = {}
    for case in fx.cases:
        res, snap = run_case(fx, case)
        results.append(res)
        if snap is not None:
            snapshots[res.case_id] = snap
    xp: dict = {"enabled": cross_process}
    if cross_process and snapshots:
        xp.update(cross_process_replay(snapshots))
    for res in results:
        if res.replay is None:
            continue
        res.replay.cross_process_required = cross_process
        if cross_process:
            res.replay.cross_process = (xp.get("results") or {}).get(res.case_id, {"error": xp.get("error", "no result")})
        _compare_replay(res.replay)
    return BenchmarkRun(
        fixtures=fx,
        results=results,
        engine_version=ENGINE_VERSION,
        cross_process={k: v for k, v in xp.items() if k != "results"},
        commit=git_commit(fx.root),
        strict=strict,
    )
