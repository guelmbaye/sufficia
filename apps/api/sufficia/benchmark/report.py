"""Benchmark and replay reports (Document 06 §103, §108, §145).

Every number in these reports is computed from a :class:`~.runner.BenchmarkRun`; nothing is
hard-coded. Reports are written as Markdown for judges and JSON for tooling:

    docs/evidence/01-benchmark-report.md   docs/evidence/benchmark.json
    docs/evidence/06-replay-report.md      docs/evidence/replay.json
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..canonical.canonicalize import format_datetime, format_decimal
from .runner import BenchmarkRun, CaseResult

BENCHMARK_MD = "01-benchmark-report.md"
BENCHMARK_JSON = "benchmark.json"
REPLAY_MD = "06-replay-report.md"
REPLAY_JSON = "replay.json"


def _utc(dt: datetime | None) -> datetime:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _pct(value: Decimal) -> str:
    return f"{format_decimal(value)}%"


def _json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return format_decimal(o) if o.is_finite() else str(o)
    if isinstance(o, datetime):
        return format_datetime(o)
    raise TypeError(type(o).__name__)


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def _repo_root(run: BenchmarkRun) -> Path:
    root = run.fixtures.root.resolve()
    return root.parents[1] if len(root.parents) > 1 else root


# =============================================================================================
# JSON
# =============================================================================================


def _case_json(r: CaseResult) -> dict:
    d = r.decision
    return {
        "case_id": r.case_id,
        "category": r.category,
        "description": r.description,
        "contract_fixture": r.contract_fixture,
        "evaluation_time": r.evaluation_time,
        "expected": {
            "decision": r.expected.get("decision"),
            "reason_codes": r.expected.get("reason_codes"),
            "failed_requirements": r.expected.get("failed_requirements"),
        },
        "actual": {
            "decision": r.actual.get("decision"),
            "reason_codes": r.actual.get("reason_codes"),
            "failed_requirements": r.actual.get("failed_requirements"),
            "causes": r.actual.get("causes"),
        },
        "result": {
            "passed": r.passed,
            "decision_correct": r.decision_correct,
            "reason_correct": r.reason_correct,
            "expectations_met": r.expectations_met,
            "explanation_faithful": r.explanation_faithful,
            "replay_matched": r.replay_matched,
        },
        "checks": r.checks,
        "failures": r.failures(),
        "advisories": r.advisories,
        "messages": [
            {"code": x.code.value, "requirement_id": x.requirement_id, "message": x.message} for x in (d.reasons if d else [])
        ],
        "contract_hash": d.contract_hash if d else None,
        "evidence_root": d.evidence_root if d else None,
        "fingerprint": d.fingerprint if d else None,
        "decision_id": d.decision_id if d else None,
        "authority_window": (
            {
                "issued_at": d.authority_window.issued_at,
                "expires_at": d.authority_window.expires_at,
                "ttl_seconds": d.authority_window.ttl_seconds,
                "bounded_by": d.authority_window.bounded_by,
            }
            if d and d.authority_window
            else None
        ),
        "authority": r.authority,
    }


def benchmark_json(run: BenchmarkRun, generated_at: datetime | None = None) -> dict:
    m = run.metrics
    return {
        "report": "sufficia-benchmark",
        "report_version": 1,
        "engine_version": run.engine_version,
        "benchmark_version": run.fixtures.benchmark_version,
        "fixture_digest": run.fixtures.digest,
        "generated_at": _utc(generated_at),
        "commit": run.commit,
        "passed": run.ok,
        "strict": run.strict,
        "total": m["total_cases"],
        "metrics": m,
        "categories": run.categories(),
        "global_problems": run.global_problems(),
        "failures": [{"case_id": r.case_id, "category": r.category, "failures": r.failures()} for r in run.results if not r.passed],
        "advisories": [{"case_id": r.case_id, "advisories": r.advisories} for r in run.results if r.advisories],
        "cases": [_case_json(r) for r in run.results],
    }


def replay_json(run: BenchmarkRun, generated_at: datetime | None = None) -> dict:
    reps = [r.replay for r in run.results if r.replay is not None]
    fields = ("state", "reason_codes", "contract_hash", "evidence_root", "fingerprint", "decision_hash")
    return {
        "report": "sufficia-replay",
        "report_version": 1,
        "engine_version": run.engine_version,
        "benchmark_version": run.fixtures.benchmark_version,
        "generated_at": _utc(generated_at),
        "commit": run.commit,
        "protocol": [
            "evaluate the case",
            "persist the decision digest",
            "persist canonical inputs (contract + evidence set + evaluation time + engine version) with canonical_json",
            "reload the snapshot with sufficia.canonical.canonicalize.loads",
            "replay in-process and in a separate Python process",
            "compare state, reason codes, contract hash, evidence root, fingerprint and canonical decision hash",
        ],
        "cross_process": run.cross_process,
        "attempted": run.total,
        "replayed": len(reps),
        "matched": run.metrics["replay_matched"],
        "replay_fidelity_pct": run.metrics["replay_fidelity_pct"],
        "field_matches": {f: sum(1 for x in reps if x.field_match(f)) for f in fields},
        "mismatches": [{"case_id": x.case_id, "mismatches": x.mismatches} for x in reps if x.mismatches]
        + [{"case_id": r.case_id, "mismatches": [f"not replayed: {r.error}"]} for r in run.results if r.replay is None],
        "cases": [
            {
                "case_id": x.case_id,
                "snapshot_sha256": x.snapshot_sha256,
                "snapshot_bytes": x.snapshot_bytes,
                "original": x.original,
                "in_process": x.in_process,
                "cross_process": x.cross_process,
                "matched": x.matched and x.decision_hash_match,
            }
            for x in reps
        ],
    }


# =============================================================================================
# Markdown
# =============================================================================================


def _yes(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def benchmark_markdown(run: BenchmarkRun, generated_at: datetime | None = None) -> str:
    m = run.metrics
    fx = run.fixtures
    repo = _repo_root(run)
    lines: list[str] = []
    add = lines.append
    add("# 01 — Decision Benchmark Report")
    add("")
    add("> Generated by `python -m sufficia.benchmark` from the committed fixtures. Do not edit by hand:")
    add("> every number below is computed from the Decision Engine's actual output on this run.")
    add("")
    add("| Field | Value |")
    add("|---|---|")
    add(f"| Result | **{'PASS' if run.ok else 'FAIL'}** |")
    add(f"| Engine version | `{run.engine_version}` |")
    add(f"| Benchmark version | `{fx.benchmark_version}` |")
    add(f"| Fixture digest | `{fx.digest}` |")
    add(f"| Date (UTC) | {format_datetime(_utc(generated_at))} |")
    add(f"| Commit | `{run.commit}` |")
    add(f"| Fixtures | `{_rel(fx.root, repo)}` |")
    add(f"| Total cases | {m['total_cases']} |")
    add("")
    add("## Headline metrics")
    add("")
    add("| Metric | Result | Definition |")
    add("|---|---|---|")
    add(f"| Decision correctness | **{m['decision_correct']} / {m['total_cases']}** ({_pct(m['decision_correctness_pct'])}) | produced state == expected state |")
    add(f"| Reason fidelity | **{m['reason_correct']} / {m['total_cases']}** ({_pct(m['reason_fidelity_pct'])}) | exact ordered reason codes, failed requirements and invalidity/acquisition causes |")
    add(f"| Explanation fidelity | **{m['explanation_faithful']} / {m['total_cases']}** ({_pct(m['explanation_fidelity_pct'])}) | every message is the reason template over the structured detail, and that detail matches facts recomputed from the raw inputs |")
    add(f"| Replay fidelity | **{m['replay_matched']} / {m['total_cases']}** ({_pct(m['replay_fidelity_pct'])}) | canonical snapshot replayed in-process and cross-process: same state, reason codes, hashes, fingerprint and decision hash |")
    add(f"| Full expectation match | {m['expectations_met']} / {m['total_cases']} ({_pct(m['expectation_match_pct'])}) | additionally: causes, authority expiry, per-requirement status, evidence statuses, details, metrics and pinned messages |")
    add(f"| Authorizations issued for AUTHORIZE | {m['authorizations_issued']} (verified: {m['authorizations_verified']}) | `build_authorization` + `verify_binding`; expiry <= earliest supporting evidence expiry |")
    add(f"| Non-AUTHORIZE decisions refused | {m['non_authorize_refused']} / {m['non_authorize_decisions']} | `build_authorization` raises `EVIDENCE_AUTHORIZATION_REQUIRED` |")
    add(f"| Unauthorized authorizations | **{m['unauthorized_authorizations']}** | authorization produced for a non-AUTHORIZE decision |")
    add("")
    add("Decision correctness means the deterministic engine implements the explicitly defined benchmark")
    add("semantics. It is not a statistical real-world accuracy claim (Document 06 §22).")
    add("")
    problems = run.global_problems()
    if problems:
        add("## Benchmark integrity problems")
        add("")
        for p in problems:
            add(f"- {p}")
        add("")
    add("## Results by category")
    add("")
    add("| Category | Expected state | Cases | Decision | Reason | Explanation | Replay | Passed |")
    add("|---|---|---|---|---|---|---|---|")
    for c in run.categories():
        exp = c["expected_state"] if isinstance(c["expected_state"], str) else ", ".join(c["expected_state"])
        n = c["cases"]
        add(
            f"| {c['category']} | {exp} | {n} | {c['decision_correct']}/{n} | {c['reason_correct']}/{n} | "
            f"{c['explanation_faithful']}/{n} | {c['replay_matched']}/{n} | {c['passed']}/{n} |"
        )
    add("")
    add("## Failed cases")
    add("")
    failed = [r for r in run.results if not r.passed]
    if not failed:
        add("None.")
    for r in failed:
        add(f"### `{r.case_id}` ({r.category})")
        add("")
        for f in r.failures():
            add(f"- {f}")
        add("")
    add("")
    add("## Advisories (non-gating)")
    add("")
    adv = [r for r in run.results if r.advisories]
    if not adv:
        add("None.")
    else:
        add("Findings that do not change the decision but that a reviewer should know about. Run with")
        add("`--strict` to make them fail the benchmark.")
        add("")
        for r in adv:
            for a in r.advisories:
                add(f"- `{r.case_id}`: {a}")
    add("")
    add("## Case list")
    add("")
    add("| Case | Expected | Actual | Reason codes | Failed requirements | Result |")
    add("|---|---|---|---|---|---|")
    for r in run.results:
        add(
            f"| `{r.case_id}` | {r.expected.get('decision')} | {r.actual.get('decision', 'ERROR')} | "
            f"{', '.join(r.actual.get('reason_codes', [])) or '-'} | {', '.join(r.actual.get('failed_requirements', [])) or '-'} | "
            f"{_yes(r.passed)} |"
        )
    add("")
    add("## Explanations produced")
    add("")
    add("One representative explanation per case (the engine's own `Reason.message`).")
    add("")
    for r in run.results:
        if r.decision is None:
            continue
        msgs = " ".join(x.message for x in r.decision.reasons)
        add(f"- `{r.case_id}` — **{r.decision.state.value}**: {msgs}")
    add("")
    add("## Authority windows (AUTHORIZE cases)")
    add("")
    add("| Case | Evaluated at | Expires at | TTL (s) | Bounded by | Earliest evidence expiry | Authorization id |")
    add("|---|---|---|---|---|---|---|")
    for r in run.results:
        d = r.decision
        if d is None or d.authority_window is None:
            continue
        w = d.authority_window
        earliest = r.authority.get("earliest_evidence_expiry")
        add(
            f"| `{r.case_id}` | {format_datetime(w.issued_at)} | {format_datetime(w.expires_at)} | {format_decimal(w.ttl_seconds)} | "
            f"`{w.bounded_by}` | {format_datetime(earliest) if earliest else '-'} | `{str(r.authority.get('authorization_id', '-'))[:18]}…` |"
        )
    add("")
    add("## How to reproduce")
    add("")
    add("```bash")
    add("cd apps/api")
    add("python -m sufficia.benchmark            # exits non-zero on any mismatch")
    add("python ../../tests/benchmark/generate_fixtures.py --check   # fixtures match their generator")
    add("```")
    add("")
    return "\n".join(lines)


def replay_markdown(run: BenchmarkRun, generated_at: datetime | None = None) -> str:
    rj = replay_json(run, generated_at)
    lines: list[str] = []
    add = lines.append
    add("# 06 — Deterministic Replay Report")
    add("")
    add("> Generated by `python -m sufficia.benchmark`. Every value is computed on this run.")
    add("")
    add("| Field | Value |")
    add("|---|---|")
    add(f"| Engine version | `{run.engine_version}` |")
    add(f"| Benchmark version | `{run.fixtures.benchmark_version}` |")
    add(f"| Date (UTC) | {format_datetime(_utc(generated_at))} |")
    add(f"| Commit | `{run.commit}` |")
    xp = run.cross_process
    if xp.get("enabled"):
        state = f"separate process (pid {xp.get('pid')}, parent pid {xp.get('parent_pid')})" if not xp.get("error") else f"FAILED: {xp['error']}"
    else:
        state = "disabled"
    add(f"| Cross-process replay | {state} |")
    add("")
    add("## Protocol")
    add("")
    for i, step in enumerate(rj["protocol"], 1):
        add(f"{i}. {step}")
    add("")
    add("Replay uses only the original contract snapshot, the original evidence items, the original")
    add("evaluation timestamp and the original engine version — never current APIs, prices, block state")
    add("or a new LLM call (Document 06 §55).")
    add("")
    add("## Summary")
    add("")
    add("| Metric | Result |")
    add("|---|---|")
    add(f"| Decisions replayed | {rj['replayed']} / {rj['attempted']} |")
    labels = {
        "state": "State matches",
        "reason_codes": "Reason-code matches",
        "contract_hash": "Contract-hash matches",
        "evidence_root": "Evidence-root matches",
        "fingerprint": "Fingerprint matches",
        "decision_hash": "Canonical decision-hash matches",
    }
    for k, label in labels.items():
        add(f"| {label} | {rj['field_matches'][k]} / {rj['replayed']} |")
    add(f"| Mismatches | {len(rj['mismatches'])} |")
    add(f"| **Replay fidelity** | **{_pct(rj['replay_fidelity_pct'])}** |")
    add("")
    add("## Mismatches")
    add("")
    if not rj["mismatches"]:
        add("None.")
    for mm in rj["mismatches"]:
        add(f"- `{mm['case_id']}`: " + "; ".join(mm["mismatches"]))
    add("")
    add("## Replayed decisions")
    add("")
    add("| Case | State | Contract hash | Evidence root | Fingerprint | Match |")
    add("|---|---|---|---|---|---|")
    for c in rj["cases"]:
        o = c["original"]
        add(
            f"| `{c['case_id']}` | {o['state']} | `{o['contract_hash'][:14]}…` | `{o['evidence_root'][:14]}…` | "
            f"`{o['fingerprint'][:14]}…` | {'MATCH' if c['matched'] else 'MISMATCH'} |"
        )
    add("")
    return "\n".join(lines)


# =============================================================================================
# Writing
# =============================================================================================


def write_reports(run: BenchmarkRun, reports_dir: Path, generated_at: datetime | None = None) -> dict[str, Path]:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    when = _utc(generated_at or run.generated_at)
    paths = {
        "benchmark_md": reports_dir / BENCHMARK_MD,
        "benchmark_json": reports_dir / BENCHMARK_JSON,
        "replay_md": reports_dir / REPLAY_MD,
        "replay_json": reports_dir / REPLAY_JSON,
    }
    paths["benchmark_md"].write_text(benchmark_markdown(run, when), encoding="utf-8", newline="\n")
    paths["benchmark_json"].write_text(
        json.dumps(benchmark_json(run, when), indent=2, default=_json_default) + "\n", encoding="utf-8", newline="\n"
    )
    paths["replay_md"].write_text(replay_markdown(run, when), encoding="utf-8", newline="\n")
    paths["replay_json"].write_text(
        json.dumps(replay_json(run, when), indent=2, default=_json_default) + "\n", encoding="utf-8", newline="\n"
    )
    return paths


def console_summary(run: BenchmarkRun) -> str:
    m = run.metrics
    lines = [
        f"SUFFICIA benchmark {run.fixtures.benchmark_version} | engine {run.engine_version} | commit {run.commit}",
        f"  decision correctness   {m['decision_correct']}/{m['total_cases']} ({_pct(m['decision_correctness_pct'])})",
        f"  reason fidelity        {m['reason_correct']}/{m['total_cases']} ({_pct(m['reason_fidelity_pct'])})",
        f"  explanation fidelity   {m['explanation_faithful']}/{m['total_cases']} ({_pct(m['explanation_fidelity_pct'])})",
        f"  replay fidelity        {m['replay_matched']}/{m['total_cases']} ({_pct(m['replay_fidelity_pct'])})",
        f"  full expectation match {m['expectations_met']}/{m['total_cases']}",
        f"  unauthorized authorizations {m['unauthorized_authorizations']}; non-AUTHORIZE refused "
        f"{m['non_authorize_refused']}/{m['non_authorize_decisions']}; authorizations verified "
        f"{m['authorizations_verified']}/{m['authorizations_issued']}",
    ]
    for c in run.categories():
        lines.append(f"  - {c['category']:<10} {c['passed']}/{c['cases']} passed (expected {c['expected_state']})")
    for p in run.global_problems():
        lines.append(f"  ! {p}")
    for r in run.results:
        if not r.passed:
            lines.append(f"  x {r.case_id}: " + " | ".join(r.failures()))
    if m["advisories"]:
        lines.append(f"  advisories: {m['advisories']} (non-gating{'; STRICT mode: gating' if run.strict else ''})")
        for r in run.results:
            for a in r.advisories:
                lines.append(f"    ~ {r.case_id}: {a}")
    lines.append("RESULT: " + ("PASS" if run.ok else "FAIL"))
    return "\n".join(lines)
