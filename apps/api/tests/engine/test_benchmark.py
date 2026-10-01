"""Runs the canonical 50-case benchmark end to end and checks that its fidelity checks have teeth
(mutation tests: a wrong expectation, a tampered explanation, a tampered fixture and a replay
divergence must each be detected)."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from sufficia.benchmark import __main__ as cli
from sufficia.benchmark import runner
from sufficia.benchmark.report import benchmark_json, replay_json, write_reports
from sufficia.domain.enums import DecisionState

REPO = Path(__file__).resolve().parents[4]
BENCH = REPO / "tests" / "benchmark"


@pytest.fixture(scope="module")
def run():
    return runner.run_benchmark(BENCH)


def test_benchmark_is_50_of_50_with_full_fidelity(run):
    m = run.metrics
    assert run.global_problems() == []
    assert m["total_cases"] == 50
    assert m["decision_correct"] == 50 and m["decision_correctness_pct"] == Decimal("100.00")
    assert m["reason_correct"] == 50 and m["reason_fidelity_pct"] == Decimal("100.00")
    assert m["explanation_faithful"] == 50 and m["explanation_fidelity_pct"] == Decimal("100.00")
    assert m["replay_matched"] == 50 and m["replay_fidelity_pct"] == Decimal("100.00")
    assert m["expectations_met"] == 50
    assert m["unauthorized_authorizations"] == 0
    assert m["authorizations_issued"] == m["authorizations_verified"] == 10
    assert m["non_authorize_refused"] == m["non_authorize_decisions"] == 40
    assert run.ok, [(r.case_id, r.failures()) for r in run.results if not r.passed]


def test_frozen_distribution(run):
    cats = {c["category"]: (c["expected_state"], c["cases"], c["passed"]) for c in run.categories()}
    assert cats == {
        "valid": ("AUTHORIZE", 10, 10),
        "missing": ("ABSTAIN", 10, 10),
        "stale": ("REFRESH", 10, 10),
        "threshold": ("DENY", 10, 10),
        "conflict": ("ESCALATE", 5, 5),
        "malformed": ("ABSTAIN", 5, 5),
    }


def test_every_state_is_exercised_and_replayed_cross_process(run):
    assert {r.decision.state for r in run.results} == set(DecisionState)
    assert run.cross_process.get("enabled") and not run.cross_process.get("error")
    assert run.cross_process["pid"] != run.cross_process["parent_pid"]
    assert all(r.replay.cross_process and "error" not in r.replay.cross_process for r in run.results)


def test_authorize_cases_expire_no_later_than_supporting_evidence(run):
    for r in run.results:
        if r.decision.state == DecisionState.AUTHORIZE:
            a = r.authority
            assert a["expires_at"] > r.decision.evaluated_at
            assert a["expires_at"] <= a["earliest_evidence_expiry"]
            assert a["contract_hash"] == r.decision.contract_hash and a["evidence_root"] == r.decision.evidence_root


def test_fixtures_match_generator():
    proc = subprocess.run([sys.executable, str(BENCH / "generate_fixtures.py"), "--check"], capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_generator_never_calls_the_engine():
    text = (BENCH / "generate_fixtures.py").read_text(encoding="utf-8")
    assert "sufficia" not in text.split('"""', 2)[2].split("BENCHMARK_VERSION")[0]  # no imports of the package
    assert "import sufficia" not in text and "from sufficia" not in text


def test_reports_are_written_and_computed(tmp_path, run):
    paths = write_reports(run, tmp_path)
    bj = json.loads(paths["benchmark_json"].read_text(encoding="utf-8"))
    assert bj["total"] == 50 and bj["passed"] is True
    assert bj["metrics"]["reason_fidelity_pct"] == "100"
    assert bj["engine_version"] == run.engine_version and bj["benchmark_version"] == "1.0.0"
    assert bj["commit"] == run.commit and bj["failures"] == []
    assert len(bj["cases"]) == 50
    rj = json.loads(paths["replay_json"].read_text(encoding="utf-8"))
    assert rj["matched"] == rj["attempted"] == 50 and rj["mismatches"] == []
    md = paths["benchmark_md"].read_text(encoding="utf-8")
    assert "**50 / 50** (100%)" in md and "| threshold | DENY | 10 |" in md
    assert "Replay fidelity** | **100%**" in paths["replay_md"].read_text(encoding="utf-8")


def test_cli_exit_codes(tmp_path, capsys):
    assert cli.main(["--cases", str(BENCH), "--reports", str(tmp_path / "r"), "--no-cross-process"]) == 0
    assert (tmp_path / "r" / "01-benchmark-report.md").exists()
    assert "RESULT: PASS" in capsys.readouterr().out
    assert cli.main(["--cases", str(tmp_path / "nowhere"), "--no-reports"]) == 2
    assert cli.main(["--cases", str(BENCH), "--no-reports", "--no-cross-process", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 50


# ---------------------------------------------------------------------------------------------
# mutation tests: the checks must catch deliberate errors
# ---------------------------------------------------------------------------------------------


def _copy_bench(tmp_path) -> Path:
    dst = tmp_path / "bench"
    shutil.copytree(BENCH, dst, ignore=shutil.ignore_patterns("fixtures", "*.py", "*.md"))
    return dst


def _rewrite(path: Path, mutate):
    data = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    mutate(data)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def test_wrong_expected_state_is_detected(tmp_path):
    bench = _copy_bench(tmp_path)
    _rewrite(bench / "cases" / "stale_005.json", lambda d: d["expected"].update(decision="DENY", reason_codes=["THRESHOLD_NOT_MET"]))
    run = runner.run_benchmark(bench, cross_process=False)
    bad = [r for r in run.results if not r.passed]
    assert [r.case_id for r in bad] == ["stale_005"]
    assert not bad[0].decision_correct and not bad[0].reason_correct
    assert run.metrics["decision_correct"] == 49 and not run.ok
    assert any("manifest" in p for p in run.global_problems())  # the edit also breaks the digest


def test_wrong_reason_order_or_cause_is_detected(tmp_path):
    bench = _copy_bench(tmp_path)
    _rewrite(bench / "cases" / "malformed_003.json",
             lambda d: d["expected"]["causes"].update(req_liquidity=["INVALID_TIMESTAMP"]))
    run = runner.run_benchmark(bench, cross_process=False)
    r = {x.case_id: x for x in run.results}["malformed_003"]
    assert r.decision_correct and not r.reason_correct


def test_tampered_fixture_is_detected_by_digest(tmp_path):
    bench = _copy_bench(tmp_path)
    _rewrite(bench / "evidence" / "liquidity_valid_01.json", lambda d: d["claim"].update(value=718001))
    run = runner.run_benchmark(bench, cross_process=False)
    assert any("digest" in p for p in run.global_problems()) and not run.ok


def test_distribution_is_enforced(tmp_path):
    bench = _copy_bench(tmp_path)
    (bench / "cases" / "conflict_005.json").unlink()
    run = runner.run_benchmark(bench, cross_process=False)
    probs = run.global_problems()
    assert any("exactly 50" in p for p in probs) and any("conflict" in p for p in probs)


def test_unfaithful_explanation_is_detected():
    fx = runner.load_fixtures(BENCH)
    for case_id, fake in [
        ("threshold_001", "Market conditions are risky."),
        ("stale_001", "The available liquidity observation is 12 seconds old. The Evidence Contract allows a maximum age of 30 seconds."),
        ("conflict_001", "Two valid sources disagree on available liquidity."),
        ("missing_004", "Required available liquidity evidence could not be established."),
    ]:
        case = [c for c in fx.cases if c["case_id"] == case_id][0]
        contract, evs, t = runner.build_inputs(fx, case)
        d = runner.evaluate(contract, evs, t)
        assert runner.check_explanations(contract, evs, t, d).faithful
        reasons = [r.model_copy(update={"message": fake}) for r in d.reasons]
        tampered = d.model_copy(update={"reasons": reasons})
        assert not runner.check_explanations(contract, evs, t, tampered).faithful, case_id


def test_explanation_detail_inconsistent_with_inputs_is_detected():
    """A message rendered faithfully from WRONG structured detail must still be caught (semantic check)."""
    from sufficia.decision_engine import explain

    fx = runner.load_fixtures(BENCH)
    case = [c for c in fx.cases if c["case_id"] == "threshold_001"][0]
    contract, evs, t = runner.build_inputs(fx, case)
    d = runner.evaluate(contract, evs, t)
    req = contract.requirement("req_liquidity")
    wrong = {"observed_values": ["400000"]}
    reason = d.reasons[0].model_copy(update={
        "detail": wrong, "message": explain.message_for(d.reasons[0].code, req, {"observed_values": [Decimal(400000)]})})
    tampered = d.model_copy(update={"reasons": [reason]})
    chk = runner.check_explanations(contract, evs, t, tampered)
    assert not chk.faithful and any("observed_values" in i for i in chk.issues)


def test_wrong_state_semantics_in_summary_is_detected():
    fx = runner.load_fixtures(BENCH)
    case = [c for c in fx.cases if c["case_id"] == "threshold_001"][0]
    contract, evs, t = runner.build_inputs(fx, case)
    d = runner.evaluate(contract, evs, t)
    tampered = d.model_copy(update={"summary": d.summary.replace("No execution authorization was issued.", "Proceed.")})
    assert not runner.check_explanations(contract, evs, t, tampered).faithful


def test_replay_divergence_is_detected():
    fx = runner.load_fixtures(BENCH)
    res, snapshot = runner.run_case(fx, [c for c in fx.cases if c["case_id"] == "valid_001"][0])
    assert res.replay is not None and snapshot is not None
    diverged = copy.deepcopy(res.replay)
    diverged.in_process = {**diverged.in_process, "state": "DENY"}
    diverged.cross_process = dict(diverged.original)
    runner._compare_replay(diverged)
    assert not diverged.matched and any("state" in m for m in diverged.mismatches)


def test_snapshot_is_canonical_and_self_contained():
    fx = runner.load_fixtures(BENCH)
    case = [c for c in fx.cases if c["case_id"] == "valid_010"][0]
    contract, evs, t = runner.build_inputs(fx, case)
    snap = runner.make_snapshot(contract, evs, t, "1.0.0")
    assert snap == runner.make_snapshot(contract, evs, t, "1.0.0")
    d = runner.replay_snapshot(snap)
    assert d.state == DecisionState.AUTHORIZE and d.evaluated_at == t


def test_json_reports_serialize_non_finite_and_decimals(run):
    json.dumps(benchmark_json(run), default=lambda o: str(o))
    json.dumps(replay_json(run), default=lambda o: str(o))
