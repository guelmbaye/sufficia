# SUFFICIA canonical benchmark (v1.0.0)

Fifty deterministic, human-inspectable decision cases for the Evidence Gate Decision Engine
(Document 06 §7-21). The dataset is frozen: it is not expanded to claim a larger number.

| Category | Cases | Expected state |
|---|---|---|
| `valid` | 10 | AUTHORIZE |
| `missing` | 10 | ABSTAIN |
| `stale` | 10 | REFRESH |
| `threshold` | 10 | DENY |
| `conflict` | 5 | ESCALATE |
| `malformed` | 5 | ABSTAIN (with specific invalidity causes) |
| **Total** | **50** | |

## Run it

```bash
cd apps/api
python -m sufficia.benchmark                 # writes docs/evidence/{01-benchmark-report.md,benchmark.json,06-replay-report.md,replay.json}
python -m sufficia.benchmark --json          # full JSON report on stdout
python -m sufficia.benchmark --strict        # advisories (non-gating findings) also fail the run
python -m sufficia.benchmark --cases DIR --reports DIR
python ../../tests/benchmark/generate_fixtures.py --check   # committed JSON == generator output
```

The command exits non-zero if any case mismatches its committed expectation, any explanation is
unfaithful, any replay differs, or an authorization could be built for a non-AUTHORIZE decision.
It never touches the network, the clock (the evaluation time is in each case) or an LLM.

## Layout

```
tests/benchmark/
  generate_fixtures.py   the ONLY source of truth; writes everything below
  manifest.json          version, distribution, per-file sha256 and the fixture digest
  contracts/             Evidence Contracts
    buy_token_v1.json      inflow > $1,000,000; liquidity > $500,000; price EXISTS; max age 30 s each;
                           minimum_required_evidence 3; evidence budget 0.25 USDC; TTL 30 s; tolerance 10%
    buy_token_gte_v1.json  identical but liquidity >= $500,000 (operator-boundary cases)
    buy_token_2src_v1.json identical but liquidity needs 2 observations (minimum_items = 2; minimum evidence 4)
  evidence/              one Evidence Item per file, referenced by name from cases
  actions/               the exact CanonicalAction an AUTHORIZE binds to (nonce/deadline come from the decision)
  cases/                 one case per file
```

Do not edit the JSON by hand: change `generate_fixtures.py` and re-run it. The runner recomputes the
fixture digest and fails if it does not match `manifest.json`.

## Conventions

* **Absolute timestamps only.** The default evaluation time is `2026-10-05T18:42:15Z`; ages in the
  descriptions are derived from the absolute `observed_at` values.
* **Numbers are JSON numbers**, loaded with `json.loads(..., parse_float=Decimal)` (exact decimals, no
  floats). A numeric *string* such as `"1340000"` is deliberately `INVALID_EVIDENCE_TYPE`.
* JSON has no NaN: non-finite numbers use the canonical tag `{"$nonfinite": "NaN"}` (same convention
  as `sufficia.canonical.canonicalize`).
* Expectations are written **by hand** in `generate_fixtures.py`, never computed by calling the engine
  (Document 06 §9: expected output is committed before benchmark execution).

## Case format

```json
{
  "case_id": "threshold_001",
  "category": "threshold",
  "description": "Canonical Winning Moment: valid, fresh liquidity of $92,440 fails '> $500,000'.",
  "tags": ["canonical", "threshold"],
  "contract_fixture": "buy_token_v1",
  "evaluation_time": "2026-10-05T18:42:15Z",
  "evidence": ["inflow_valid_01", "liquidity_low_92440", "price_valid_01"],
  "acquisition_failures": [],
  "expected": {
    "decision": "DENY",
    "reason_codes": ["THRESHOLD_NOT_MET"],
    "failed_requirements": ["req_liquidity"],
    "requirement_status": {"req_inflow": "PASS", "req_liquidity": "FAIL", "req_price": "PASS"},
    "reason_detail": {"req_liquidity": {"observed_values": ["92440"]}},
    "messages": {"req_liquidity": "Available liquidity is $92,440. The Evidence Contract requires more than $500,000."}
  }
}
```

Required keys: `case_id`, `category`, `description`, `contract_fixture`, `evaluation_time`, `evidence`
(fixture names or inline Evidence Item objects), `expected.decision`, `expected.reason_codes`,
`expected.failed_requirements`. Optional: `acquisition_failures` (inline `AcquisitionFailure` objects),
`action_fixture` (default `buy_token_x_v1`) and these `expected` keys:

| Key | Compared with |
|---|---|
| `decision` | `Decision.state` |
| `reason_codes` | `Decision.reason_codes` (exact, ordered) |
| `failed_requirements` | requirement ids of the decisive `Decision.reasons`, in order |
| `causes` | `{req_id: [codes]}` vs the requirement's `Reason.causes` (exact, ordered) |
| `authority_expires_at` / `authority_bounded_by` / `authority_ttl_seconds` | `Decision.authority_window` |
| `requirement_status` | `{req_id: PASS/FAIL/UNKNOWN}` vs `requirement_results` |
| `evidence` | `{evidence_id: {status, reason_codes}}` vs `evidence_assessments` |
| `reason_detail` | subset of a reason's structured `detail` (`_decision` = reason without requirement) |
| `metrics` | `Decision` scalar fields (`coverage`, `evidence_count`, `independent_source_count`, ...) |
| `messages` | exact human message of a reason (pins the UI wording for key cases) |

## What each run measures

* **Decision correctness** — state == expected state.
* **Reason fidelity** — exact ordered reason codes, failed requirements and causes.
* **Explanation fidelity** — for every reason: (T) the message equals
  `explain.message_for(code, requirement, structured detail)`; (S) the structured detail agrees with
  facts recomputed *independently from the raw inputs* (failing observed values and required phrase for
  `THRESHOLD_NOT_MET`; the freshest stale item, its exact age and the max age for `EVIDENCE_STALE`;
  the worst conflicting pair, providers, values, relative difference and tolerance for
  `CRITICAL_SOURCE_CONFLICT`; every invalidity/acquisition cause phrased for `MISSING_REQUIRED_EVIDENCE`;
  budget and recomputed spend for `EVIDENCE_BUDGET_EXHAUSTED`); (C) the explained requirements are exactly
  the requirements that drive the state, and the summary carries the state semantics. Display-precision
  problems (e.g. "30 seconds old; maximum 30 seconds") are reported as non-gating *advisories*.
* **Replay fidelity** — the canonical inputs (contract + evidence set + evaluation time + engine version)
  are serialized with `canonical_json`, reloaded with `canonicalize.loads` and re-evaluated in-process and
  in a separate Python process; state, reason codes, contract hash, evidence root, fingerprint and the
  canonical hash of the whole decision must match.
* **Authority** — every AUTHORIZE yields an authorization through `build_authorization`, verified with
  `verify_binding`, bound to the recomputed contract hash / evidence root / action hash and expiring after
  the evaluation time but no later than the earliest supporting-evidence expiry; every other state is
  refused with `EVIDENCE_AUTHORIZATION_REQUIRED`.

## Cases

| Case | Expected | What it pins |
|---|---|---|
| valid_001 | AUTHORIZE | canonical valid set; authority bounded by the oldest item (25 s, not 30 s) |
| valid_002 | AUTHORIZE | age 29.999 s is fresh; 1 ms authority window |
| valid_003 | AUTHORIZE | liquidity $500,000.01 > $500,000 |
| valid_004 | AUTHORIZE | `>=` contract: liquidity exactly $500,000 |
| valid_005 | AUTHORIZE | two liquidity sources 3.2% apart (no conflict) |
| valid_006 | AUTHORIZE | two liquidity sources exactly 10.0% apart (conflict needs > 10%); inflow $1,000,000.01 |
| valid_007 | AUTHORIZE | dynamic TTL: 5 s left on inflow |
| valid_008 | AUTHORIZE | dynamic TTL with sub-second precision (1.5 s) |
| valid_009 | AUTHORIZE | contract TTL bound; future timestamp within 2 s clock skew |
| valid_010 | AUTHORIZE | number formatting (2750000.50, 1.2e6, 0.4280); onchain/oracle provenance |
| missing_001 | ABSTAIN | price absent |
| missing_002 | ABSTAIN | liquidity absent |
| missing_003 | ABSTAIN | inflow RATE_LIMITED |
| missing_004 | ABSTAIN | liquidity SOURCE_UNAVAILABLE |
| missing_005 | ABSTAIN | every acquisition failed |
| missing_006 | ABSTAIN | EVIDENCE_BUDGET_EXHAUSTED via item costs (0.26 > 0.25) |
| missing_007 | ABSTAIN | EVIDENCE_BUDGET_EXHAUSTED via BUDGET_EXCEEDED acquisition failure |
| missing_008 | ABSTAIN | item for another contract (CONTRACT_MISMATCH) |
| missing_009 | ABSTAIN | item for an unknown requirement id |
| missing_010 | ABSTAIN | minimum_items 2 with a re-submitted duplicate (dedupe) |
| stale_001 | REFRESH | liquidity 92 s old |
| stale_002 | REFRESH | liquidity 30.001 s old (boundary) |
| stale_003 | REFRESH | inflow 31 s old |
| stale_004 | REFRESH | price 5 min old |
| stale_005 | REFRESH | stale AND below threshold (stale precedes DENY) |
| stale_006 | REFRESH | valid_001's evidence evaluated 40 s later |
| stale_007 | REFRESH | stale price + liquidity conflict (stale precedes ESCALATE) |
| stale_008 | REFRESH | stale liquidity + failing fresh inflow (stale precedes DENY) |
| stale_009 | REFRESH | favorable RECORDED response replayed 1 h later |
| stale_010 | REFRESH | minimum_items 2: one fresh + one stale |
| threshold_001 | DENY | canonical $92,440 < $500,000 |
| threshold_002 | DENY | liquidity exactly $500,000 with `>` |
| threshold_003 | DENY | liquidity $499,999.99 |
| threshold_004 | DENY | inflow $640,000 |
| threshold_005 | DENY | inflow exactly $1,000,000 with `>` |
| threshold_006 | DENY | negative inflow (net outflow) is valid evidence |
| threshold_007 | DENY | two failing requirements |
| threshold_008 | DENY | `>=` contract: $499,999.99 |
| threshold_009 | DENY | failing liquidity exactly 30.000 s old is still current truth |
| threshold_010 | DENY | two agreeing sources straddle the threshold: no averaging |
| conflict_001 | ESCALATE | $718,000 vs $91,000 |
| conflict_002 | ESCALATE | 10.1% apart (boundary) |
| conflict_003 | ESCALATE | conflict + failing inflow (conflict precedes DENY) |
| conflict_004 | ESCALATE | price sources 17.7% apart under EXISTS |
| conflict_005 | ESCALATE | three sources, one outlier: no majority vote |
| malformed_001 | ABSTAIN | value "more than enough" (INVALID_EVIDENCE_TYPE) |
| malformed_002 | ABSTAIN | prompt injection as value; numeric string "1340000" |
| malformed_003 | ABSTAIN | unit BTC (INVALID_UNIT); observed_at null (INVALID_TIMESTAMP) |
| malformed_004 | ABSTAIN | missing hashes / tampered short hash (PROVENANCE_MISSING) |
| malformed_005 | ABSTAIN | NaN and negative liquidity (INVALID_VALUE) |
