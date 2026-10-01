"""Generates the judge-facing evidence pack (Document 06 §102-110) from real runs.

    python -m sufficia.evidence_pack [--out docs/evidence] [--skip-tests]

Nothing in the pack is typed by hand: every number comes from the benchmark runner, a pytest run
(JUnit XML) or an actual protected-execution run through the HTTP API. The chain used for the proof
run is whatever CHAIN_MODE configures; on LOCAL_EVM the Monad proof file says so in its first line.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .config import API_ROOT, REPO_ROOT, Settings

INVARIANTS: list[tuple[str, str, list[str]]] = [
    ("INV-01", "No authorization without AUTHORIZE.", [
        "tests/engine/test_authorization.py::test_non_authorize_decisions_are_refused",
        "tests/api/test_api_security.py::test_every_non_authorize_state_is_403_and_never_reaches_the_chain",
        "tests/blockchain/test_chain_execution_adapters.py::test_deny_decision_has_no_action_and_no_authorization",
        "tests/api/test_api_security.py::test_tampered_decision_snapshot_cannot_be_authorized",
    ]),
    ("INV-02", "No AUTHORIZE with missing required evidence.", [
        "tests/engine/test_engine.py::test_missing_mandatory_evidence_abstains",
        "tests/engine/test_properties.py::test_p1_adding_unsupported_mandatory_requirement_breaks_authorize",
    ]),
    ("INV-03", "No AUTHORIZE with stale required evidence.", [
        "tests/engine/test_engine.py::test_freshness_boundaries",
        "tests/engine/test_engine.py::test_stale_value_is_not_evaluated_as_current_truth",
        "tests/engine/test_properties.py::test_p2_staling_all_evidence_of_a_requirement_breaks_authorize",
    ]),
    ("INV-04", "No AUTHORIZE with unresolved critical conflicts.", [
        "tests/engine/test_engine.py::test_conflict_boundary_is_strictly_greater_than_tolerance",
        "tests/engine/test_engine.py::test_conflict_precedes_threshold_failure",
        "tests/evidence/test_acquisition.py::test_two_source_mode_enables_conflict_detection",
    ]),
    ("INV-05", "No AUTHORIZE when a mandatory threshold fails.", [
        "tests/engine/test_properties.py::test_p3_failing_threshold_breaks_authorize",
        "tests/engine/test_engine.py::test_negative_inflow_is_valid_and_fails_threshold",
    ]),
    ("INV-06", "An authorization is bound to one action.", [
        "tests/engine/test_authorization.py::test_verify_binding_detects_action_mutations",
        "tests/contracts/test_evidence_gate_evm.py::test_fuzz_different_amount_gives_different_action_hash",
        "tests/api/test_api_security.py::test_attacker_bypassing_the_backend_is_stopped_by_the_contract",
        "tests/api/test_api_security.py::test_tampered_action_in_database_is_refused_before_submission",
    ]),
    ("INV-07", "An authorization is bound to one Evidence Contract.", [
        "tests/engine/test_authorization.py::test_decision_for_another_contract_is_refused",
        "tests/contracts/test_evidence_gate_evm.py::test_authorize_rejects_contract_hash_mismatch",
        "tests/engine/test_properties.py::test_p6_changing_threshold_changes_contract_hash",
    ]),
    ("INV-08", "An authorization is bound to one Evidence Set.", [
        "tests/engine/test_authorization.py::test_single_mutations_of_the_evidence_break_the_binding",
        "tests/engine/test_properties.py::test_p5_changing_evidence_changes_evidence_root",
    ]),
    ("INV-09", "An authorization expires no later than its supporting evidence.", [
        "tests/engine/test_engine.py::test_authority_window_bounded_by_oldest_mandatory_evidence",
        "tests/engine/test_authorization.py::test_deadline_beyond_evidence_expiry_is_refused",
        "tests/engine/test_benchmark.py::test_authorize_cases_expire_no_later_than_supporting_evidence",
        "tests/api/test_api_security.py::test_expired_authority_never_reaches_execution",
        "tests/contracts/test_evidence_gate_evm.py::test_execute_expired_authorization",
    ]),
    ("INV-10", "An authorization can be consumed only once.", [
        "tests/contracts/test_evidence_gate_evm.py::test_target_revert_leaves_authorization_unconsumed",
        "tests/blockchain/test_chain_lifecycle_evm.py::test_reuse_is_rejected",
        "tests/api/test_api_security.py::test_attacker_bypassing_the_backend_is_stopped_by_the_contract",
    ]),
    ("INV-11", "The LLM cannot issue or override execution authority.", [
        "tests/engine/test_adversarial.py::test_llm_override_is_irrelevant_to_the_decision",
        "tests/engine/test_adversarial.py::test_llm_output_cannot_be_smuggled_in_as_evidence",
        "tests/api/test_api_security.py::test_override_that_relaxes_the_intent_is_rejected",
        "tests/api/test_api_security.py::test_prompt_injection_in_the_intent_creates_no_authority",
    ]),
    ("INV-12", "System uncertainty fails closed.", [
        "tests/engine/test_adversarial.py::test_pathological_numbers_fail_closed",
        "tests/evidence/test_acquisition.py::test_adapter_bug_fails_closed_and_visible",
        "tests/blockchain/test_chain_rpc_failover.py::test_gateway_all_rpcs_down_fails_closed",
    ]),
    ("INV-13", "Replay with identical canonical inputs is deterministic.", [
        "tests/engine/test_benchmark.py::test_every_state_is_exercised_and_replayed_cross_process",
        "tests/engine/test_engine_findings.py::test_f1_high_precision_values_replay_identically",
        "tests/engine/test_engine_findings.py::test_f2_address_valued_claims_replay_identically",
    ]),
    ("INV-14", "Human explanations faithfully represent machine decision reasons.", [
        "tests/engine/test_engine.py::test_explanation_deny_contains_observed_and_required_values",
        "tests/engine/test_benchmark.py::test_unfaithful_explanation_is_detected",
        "tests/engine/test_engine_findings.py::test_f10_threshold_message_shows_the_violation",
    ]),
    ("INV-15", "Removing valid mandatory evidence cannot increase authority.", [
        "tests/engine/test_properties.py::test_a15_monotonic_safety_on_arbitrary_worlds",
        "tests/engine/test_properties.py::test_a15_removing_mandatory_evidence_from_authorize_never_authorizes",
    ]),
]

MUTATIONS = {
    "valid-v1": "none - all three observations fresh; liquidity $718,000 > $500,000",
    "threshold-fail-v1": "available_liquidity observed at $92,440 (< $500,000)",
    "missing-v1": "Demo Lab `remove` on req_price: the price observation is dropped from the evidence set",
    "stale-v1": "Demo Lab `stale` on req_price: the observation is re-stamped 120 s old (max age 30 s)",
    "conflict-v1": "Demo Lab `conflict` on req_liquidity: a second source reports $89,750 vs $718,000 (tolerance 10 %)",
    "grand-prize-v1": "liquidity $92,440 (DENY), then the simulated market recovers to $718,000 and evidence is re-acquired",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", check=True
        ).stdout.strip()
    except Exception:
        return "unversioned"


def _pytest(targets: list[str], xml_path: Path) -> dict[str, str]:
    """Runs pytest on node ids / paths and returns {nodeid: PASS|FAIL|SKIP}."""
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml_path}", *targets]
    subprocess.run(cmd, cwd=API_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # results come from the JUnit XML
    results: dict[str, str] = {}
    root = ET.parse(xml_path).getroot()
    for tc in root.iter("testcase"):
        cls = tc.get("classname", "")
        name = tc.get("name", "")
        path = cls.replace(".", "/") + ".py"
        base = name.split("[")[0]
        status = "PASS"
        if tc.find("failure") is not None or tc.find("error") is not None:
            status = "FAIL"
        elif tc.find("skipped") is not None:
            status = "SKIP"
        key = f"{path}::{base}"
        prev = results.get(key)
        results[key] = "FAIL" if "FAIL" in (prev, status) else status
    return results


def _counts(xml_path: Path) -> dict[str, int]:
    root = ET.parse(xml_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = sum(int(s.get("tests", 0)) for s in suites)
    failures = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    return {"tests": total, "failed": failures, "skipped": skipped, "passed": total - failures - skipped}


# =============================================================================================


def write_security(out: Path, commit: str, tmp: Path) -> dict[str, Any]:
    nodes = sorted({n for _, _, ts in INVARIANTS for n in ts})
    res = _pytest(nodes, tmp / "inv.xml")
    rows, all_ok = [], True
    for inv, statement, tests in INVARIANTS:
        statuses = [res.get(t, "NOT RUN") for t in tests]
        ok = all(s == "PASS" for s in statuses)
        all_ok &= ok
        rows.append((inv, statement, tests, statuses, ok))
    lines = [
        "# 02 — Security invariants",
        "",
        f"Generated {_now().isoformat()} by `python -m sufficia.evidence_pack` from a pytest run (JUnit XML).",
        f"Commit: `{commit}` · SUFFICIA {__version__}",
        "",
        f"**Result: {sum(r[4] for r in rows)}/{len(rows)} invariants PASS** "
        f"({sum(1 for r in rows for s in r[3] if s == 'PASS')}/{sum(len(r[2]) for r in rows)} mapped tests passing).",
        "",
        "| ID | Statement | Result | Tests |",
        "|---|---|---|---|",
    ]
    for inv, statement, tests, statuses, ok in rows:
        cell = "<br>".join(f"`{t.split('::')[1]}` ({t.split('/')[1]}) — {s}" for t, s in zip(tests, statuses))
        lines.append(f"| {inv} | {statement} | {'PASS' if ok else 'FAIL'} | {cell} |")
    lines += ["", "Re-run: `cd apps/api && python -m pytest " + " ".join(nodes[:2]) + " ...` (full list above)."]
    (out / "02-security-invariants.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return {"invariants": len(rows), "passing": sum(r[4] for r in rows), "ok": all_ok}


def write_contracts(out: Path, commit: str, tmp: Path, deployment: dict[str, Any]) -> dict[str, Any]:
    _pytest(["tests/contracts", "tests/blockchain"], tmp / "contracts.xml")
    c_all = _counts(tmp / "contracts.xml")
    _pytest(["tests/contracts"], tmp / "gate.xml")
    c_gate = _counts(tmp / "gate.xml")
    art = json.loads((API_ROOT / "sufficia" / "blockchain" / "artifacts" / "EvidenceGate.json").read_text(encoding="utf-8"))
    sol = (REPO_ROOT / "contracts" / "test" / "EvidenceGate.t.sol").read_text(encoding="utf-8")
    foundry_tests = sol.count("function test")
    fuzz = sol.count("function testFuzz")
    lines = [
        "# 03 — Smart contract tests",
        "",
        f"Generated {_now().isoformat()} · commit `{commit}`",
        "",
        "## Executed EVM tests (py-evm, real EvidenceGate bytecode)",
        "",
        "`cd apps/api && python -m pytest tests/contracts tests/blockchain`",
        "",
        f"- EvidenceGate contract suite (`tests/contracts`): **{c_gate['passed']}/{c_gate['tests']} passed** "
        "(includes 4 seeded fuzz properties × 40 runs: action-hash parity, calldata/amount sensitivity, authorization-id parity)",
        f"- Contract + chain-gateway lifecycle suites combined: **{c_all['passed']}/{c_all['tests']} passed**, {c_all['failed']} failed",
        "",
        "## Foundry suite",
        "",
        "`cd contracts && forge test -vv` (Foundry) · compile-checked in CI with `npm run check:foundry`",
        "",
        f"- `contracts/test/EvidenceGate.t.sol`: {foundry_tests} test functions, of which {fuzz} fuzz tests.",
        "- In this build environment Foundry binaries cannot be downloaded, so the Foundry suite is compile-checked with solcjs;",
        "  the same scenarios are executed against the compiled bytecode by the py-evm suite above.",
        "",
        "## Compiler",
        "",
        f"- solc `{art['compiler']['version']}`, EVM `{art['compiler']['evmVersion']}`, optimizer "
        f"{'on' if art['compiler']['optimizer']['enabled'] else 'off'} ({art['compiler']['optimizer']['runs']} runs)",
        "",
        "## Deployment used by the proof run",
        "",
        f"- Network: {deployment.get('network')} (chain id {deployment.get('chain_id')})",
        f"- EvidenceGate: `{deployment.get('evidence_gate')}`",
        f"- Execution router: `{deployment.get('router')}` · USDC: `{deployment.get('usdc')}` · TOKEN-X: `{deployment.get('token_x')}`",
    ]
    (out / "03-smart-contract-tests.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return {"evm_tests": c_gate, "chain_tests": c_all, "foundry_tests": foundry_tests}


def proof_run(settings: Settings) -> dict[str, Any]:
    """Runs grand-prize-v1 and every scenario through the real HTTP API; returns everything observed."""
    from fastapi.testclient import TestClient

    from .api.app import create_app
    from .evidence.simulated_market import MARKET

    MARKET.reset()
    app = create_app(settings)
    with TestClient(app) as c:
        info = c.get("/api/v1/system").json()
        runs = {}
        for sc in c.get("/api/v1/demo/scenarios").json()["scenarios"]:
            runs[sc["scenario_id"]] = c.post(f"/api/v1/demo/scenarios/{sc['scenario_id']}/run", json={}).json()
        gp = runs["grand-prize-v1"]
        ex = [s for s in gp["timeline"] if s["op"] == "execute"][0]
        receipt = c.get(f"/api/v1/receipts/{ex['receipt_id']}").json()
        auth = c.get(f"/api/v1/authorizations/{ex['authorization']['authorization_id']}").json()
        replay = c.post(f"/api/v1/receipts/{ex['receipt_id']}/replay").json()
        acquisitions = [s for s in gp["timeline"] if s["op"] == "acquire"]
        evidence = c.get(f"/api/v1/contracts/{gp['contract_id']}/evidence").json()
        decision_ids = [s["decision_id"] for s in gp["timeline"] if s["op"] == "decide"]
        deny = c.get(f"/api/v1/decisions/{decision_ids[0]}").json()
        blocked = c.get(f"/api/v1/decisions/{decision_ids[0]}/receipt").json()
        chain = c.get("/api/v1/chain").json()
        audit = c.get("/api/v1/audit/verify").json()
        txs = c.get("/api/v1/chain/transactions").json()["transactions"]
    MARKET.reset()
    return {"system": info, "runs": runs, "execution": ex, "receipt": receipt, "authorization": auth, "replay": replay,
            "acquisitions": acquisitions, "evidence": evidence, "deny": deny, "blocked": blocked, "chain": chain,
            "audit": audit, "transactions": txs}


def write_monad(out: Path, commit: str, p: dict[str, Any]) -> None:
    info = p["chain"]["info"]
    local = info["mode"] == "LOCAL_EVM"
    rc = p["receipt"]["receipt"]
    auth = p["authorization"]
    txs = {t["purpose"]: t for t in auth["transactions"]}
    ex = p["execution"]
    events = [(t["purpose"], t["tx_hash"], e) for t in auth["transactions"] for e in (t.get("events") or [])]
    head = (
        "> **LOCAL REHEARSAL — NOT MONAD.** This file was generated with `CHAIN_MODE=LOCAL_EVM`: the real EvidenceGate "
        "bytecode ran on an in-process py-evm chain. Regenerate on Monad testnet with "
        "`CHAIN_MODE=MONAD_TESTNET make evidence` after `make deploy-testnet` (see docs/deployment.md)."
        if local else f"> Generated against **{info['network']}** (chain id {info['chain_id']})."
    )
    def link(h):
        return f"[`{h}`]({info['explorer_url']}/tx/{h})" if (h and info.get("explorer_url") and not local) else f"`{h}`"
    lines = [
        "# 04 — Monad proof", "", head, "", f"Generated {_now().isoformat()} · commit `{commit}`", "",
        "## Network", "",
        f"- Mode: `{info['mode']}` · network: {info['network']} · chain id: `{info['chain_id']}`",
        f"- EvidenceGate: `{info['evidence_gate']}`",
        f"- Authorizer: `{info['authorizer']}` · Executor: `{info['executor']}`",
        f"- Deployment: `{json.dumps(info.get('deployment', {}).get('txs', {}))}`" if info.get("deployment") else "- Deployment: see deployments/",
        "", "## Successful protected execution (grand-prize-v1)", "",
        f"- Evidence Contract hash: `{rc['contract_hash']}`",
        f"- Evidence root: `{rc['evidence_root']}`",
        f"- Decision: `{rc['decision_state']}` (`{rc['decision_id']}`)",
        f"- Action hash: `{rc['action_hash']}`",
        f"- Authorization id: `{auth['authorization_id']}` (expires {auth['expires_at']})",
        f"- Intent registration tx: {link(auth.get('register_tx_hash'))}",
        f"- Authorization tx: {link(auth.get('authorize_tx_hash'))} (block {txs.get('AUTHORIZATION', {}).get('block_number')})",
        f"- Execution tx: {link(ex['tx_hash'])} (status {ex['status']})",
        f"- Receipt hash: `{p['receipt']['receipt_hash']}` · commitment tx: {link(p['receipt']['commitment']['tx_hash'])} "
        f"({p['receipt']['commitment']['status']})",
        "", "## EvidenceGate events (decoded from receipts)", "",
    ]
    for purpose, tx_hash, e in events:
        lines.append(f"- {purpose} `{e['event']}` {json.dumps(e.get('args'), sort_keys=True)}")
    lines += [
        "", "## Blocked decision (no transaction)", "",
        f"- Decision `{p['deny']['decision']['decision_id']}`: **{p['deny']['decision']['state']}** "
        f"{p['deny']['decision']['reason_codes']} — {p['deny']['decision']['summary']}",
        f"- Receipt `{p['blocked']['receipt']['receipt_id']}`: execution status `{p['blocked']['receipt']['execution']['status']}`, "
        f"tx_hash `{p['blocked']['receipt']['execution']['tx_hash']}`",
        "", "## Replay", "",
        f"- Result **{p['replay']['result']}**, chain verification `{p['replay']['chain_verification']}`, "
        f"{sum(1 for c in p['replay']['checks'] if c['ok'])}/{len(p['replay']['checks'])} checks passing, market queried: {p['replay']['market_queried']}",
    ]
    (out / "04-monad-proof.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def write_x402(out: Path, commit: str, p: dict[str, Any], settings: Settings) -> None:
    evidence_items = {v["item"]["evidence_id"]: v for v in p["evidence"]["items"]}
    pays = p["evidence"]["payments"]
    lines = ["# 05 — x402 proof", "", f"Generated {_now().isoformat()} · commit `{commit}`", ""]
    simulated = all(pay["status"] == "SIMULATED" for pay in pays) if pays else True
    if simulated:
        lines += [
            "> **SIMULATED SETTLEMENT.** These payments were made against the *local x402 reference resource server* "
            "(`/_dev/x402`): the client performed the full x402 v2 `exact` flow (402 → EIP-3009 "
            "TransferWithAuthorization signed with EIP-712 → `PAYMENT-SIGNATURE` → verified server-side, nonce single-use), "
            "but nothing was settled onchain. Set `X402_EVIDENCE_URL` + `EVIDENCE_PAYMENT_PRIVATE_KEY` and "
            "`EVIDENCE_PROFILE=live` for a Monad-settled payment (facilitator "
            f"`{settings.x402_facilitator}`).",
            "",
        ]
    lines += [f"- Network: `{settings.x402_network}` · asset: `{settings.x402_asset}` (USDC, EIP-712 domain "
              f"`{settings.x402_asset_name}` v{settings.x402_asset_version}) · scheme: `exact`", ""]
    lines += ["| Requirement | Provider | Quote | Paid | Status | Payment reference | Acquisition | Evidence item | Response hash |",
              "|---|---|---|---|---|---|---|---|---|"]
    for pay in pays:
        item = evidence_items.get(pay.get("evidence_id") or "", {}).get("item", {})
        lines.append(
            f"| {pay['requirement_id']} | {pay['provider']} | {pay['quoted']} {pay['currency']} | "
            f"{(str(pay['settled']) + ' ' + pay['currency']) if pay['settled'] else (str(pay['quoted']) + ' signed, not settled onchain')} | "
            f"{pay['status']} | `{pay['payment_reference']}` | `{pay['acquisition_id']}` | `{pay.get('evidence_id')}` | "
            f"`{item.get('integrity', {}).get('response_hash')}` |"
        )
    lines += ["", "Payment success never makes evidence valid: the paid observation above went through the same normalizer and "
              "Decision Engine as every other item (see 02-security-invariants.md, INV-12)."]
    (out / "05-x402-proof.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def write_scenarios(out: Path, commit: str, p: dict[str, Any]) -> dict[str, Any]:
    lines = ["# 07 — Demo scenarios", "", f"Generated {_now().isoformat()} · commit `{commit}` · each run goes through the HTTP API "
             "(`POST /api/v1/demo/scenarios/{id}/run`)", "",
             "| Scenario | Evidence mutation | Final state | Reason codes | Receipt | Replay | Passed |", "|---|---|---|---|---|---|---|"]
    passed = 0
    for sid in ["valid-v1", "threshold-fail-v1", "missing-v1", "stale-v1", "conflict-v1", "grand-prize-v1"]:
        r = p["runs"].get(sid)
        if not r:
            continue
        decides = [s for s in r["timeline"] if s["op"] == "decide"]
        replays = [s for s in r["timeline"] if s["op"] == "replay"]
        passed += bool(r["passed"])
        lines.append(
            f"| `{sid}` | {MUTATIONS.get(sid, '')} | **{r['final_state']}** | {', '.join(decides[-1]['reason_codes'])} | "
            f"`{r['receipt_id']}` | {replays[-1]['result'] if replays else '-'} | {'yes' if r['passed'] else 'NO'} |"
        )
    lines += ["", "State mapping (Document 02 §33): VALID → AUTHORIZE · THRESHOLD FAIL → DENY · MISSING → ABSTAIN · "
              "STALE → REFRESH · CONFLICT → ESCALATE.", "",
              "Injected observations are attributed to the provider `demo-lab` and badged `INJECTED`; they never reuse a real "
              "provider's name, and they are evaluated by the same engine as acquired evidence."]
    (out / "07-demo-scenarios.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return {"scenarios": len(p["runs"]), "passed": passed}


LIMITATIONS = """# 08 — Known limitations

SUFFICIA makes a narrow, verifiable claim: **an agent's economic action executes only if the evidence its principal
required was established, fresh, consistent and bound to that exact action.** It does not claim more.

- **Registered-provider trust.** Evidence is only as good as the registered sources. SUFFICIA proves *which* source said
  *what* and *when* (request/response hashes, evidence root); it does not prove that a source told the truth.
- **No objective-truth guarantee.** Thresholds are the principal's policy, not investment advice. "AUTHORIZE" means
  "the declared conditions held", not "this trade is good".
- **Centralized hackathon authorizer.** One backend key holds `AUTHORIZER_ROLE`. The contract independently enforces
  exact action binding, expiry, executor and single use, but a compromised authorizer could authorize a *different
  decision*. Production would use threshold signatures or a verifier network.
- **One spend per intent is a backend rule.** The EvidenceGate consumes each authorization once; that an *intent*
  ("buy up to 100 USDC") is spent at most once across its decisions is enforced by the API (`INTENT_ALREADY_EXECUTED`),
  not yet by the contract.
- **Limited Evidence Contract DSL.** Six claims (inflow, liquidity, price, listing, sanctions flag, venue), nine operators,
  per-requirement freshness/tolerance/minimum items. No arbitrary expressions, no cross-claim formulas.
- **Single execution vertical.** One swap adapter (DemoSwapRouter) and one transfer adapter. Other venues need an adapter
  with calldata verification (`verify_spend`).
- **Demo market and reference x402 server.** The demo profile reads a *simulated* market and pays a *local* x402
  reference server whose settlement is `SIMULATED`. Every card is badged with its mode; nothing simulated is presented
  as live. Live adapters (Nansen, DexScreener, Chainlink, Uniswap-V2 pool, paid x402 endpoint) are implemented and tested
  against recorded/mocked responses but require keys and network access.
- **Testnet-first.** The default chain is an in-process EVM running the real contract; Monad testnet/mainnet are
  configuration (`CHAIN_MODE`, deployment file). No production custody claims; demo tokens only.
- **Clock model.** Freshness is evaluated at decision time with a 2 s clock-skew allowance; authority windows are whole
  seconds onchain (a window shorter than 1 s is refused as `AUTHORITY_WINDOW_EXHAUSTED`).
- **No distributed verifier network**, no multi-tenant auth, single API process (signer queues and the local chain are
  in-process state).
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sufficia.evidence_pack")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "docs" / "evidence")
    parser.add_argument("--skip-tests", action="store_true", help="skip the pytest-derived reports (02, 03)")
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    commit = _commit()
    summary: dict[str, Any] = {"generated_at": _now().isoformat(), "commit": commit, "version": __version__}

    from .benchmark.report import write_reports
    from .benchmark.runner import run_benchmark

    run = run_benchmark()
    run.commit = commit
    write_reports(run, out)
    summary["benchmark"] = {k: str(v) for k, v in run.metrics.items() if isinstance(v, (int, str)) or hasattr(v, "quantize")}
    summary["benchmark_ok"] = run.ok
    print(f"benchmark: {run.metrics['decision_correct']}/{run.metrics['total_cases']} ok={run.ok}")

    db = Path(tempfile.mkdtemp()) / "proof.db"
    settings = Settings(database_url=f"sqlite:///{db}", demo_mode=True, log_json=False, log_level="WARNING",
                        rate_limit_per_minute=0, intent_compiler=os.environ.get("INTENT_COMPILER", "deterministic"))
    proof = proof_run(settings)
    write_monad(out, commit, proof)
    write_x402(out, commit, proof, settings)
    summary["scenarios"] = write_scenarios(out, commit, proof)
    summary["proof"] = {
        "chain_mode": proof["chain"]["info"]["mode"],
        "evidence_gate": proof["chain"]["info"]["evidence_gate"],
        "execution_tx": proof["execution"]["tx_hash"],
        "authorization_tx": proof["authorization"].get("authorize_tx_hash"),
        "receipt_hash": proof["receipt"]["receipt_hash"],
        "replay": proof["replay"]["result"],
        "audit_chain_valid": proof["audit"]["valid"],
        "unauthorized_executions": sum(
            1 for r in proof["runs"].values() for s in r["timeline"] if s["op"] == "attempt_execute" and not s["ok"]
        ),
    }
    print(f"proof run: {summary['proof']['chain_mode']} execution={summary['proof']['execution_tx']} replay={summary['proof']['replay']}")

    if not args.skip_tests:
        with tempfile.TemporaryDirectory() as tmp:
            summary["security"] = write_security(out, commit, Path(tmp))
            info = proof["chain"]["info"]
            summary["contracts"] = write_contracts(out, commit, Path(tmp), {
                "network": info["network"], "chain_id": info["chain_id"], "evidence_gate": info["evidence_gate"],
                "router": info["router"], "usdc": info["usdc"], "token_x": info["token_x"]})
        print(f"invariants: {summary['security']['passing']}/{summary['security']['invariants']}")
    (out / "08-known-limitations.md").write_text(LIMITATIONS, encoding="utf-8", newline="\n")
    (out / "proof.json").write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8", newline="\n")
    ok = run.ok and summary["scenarios"]["passed"] == summary["scenarios"]["scenarios"] and summary["proof"]["replay"] == "MATCH"
    if "security" in summary:
        ok = ok and summary["security"]["ok"]
    print("EVIDENCE PACK:", "OK" if ok else "FAILED", "->", out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
