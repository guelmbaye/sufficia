# 07 — Demo scenarios

Generated 2026-09-30T21:21:05.758470+00:00 · commit `e90fec6a0df4ea3694fbb7af6a20d81194f8f406` · each run goes through the HTTP API (`POST /api/v1/demo/scenarios/{id}/run`)

| Scenario | Evidence mutation | Final state | Reason codes | Receipt | Replay | Passed |
|---|---|---|---|---|---|---|
| `valid-v1` | none - all three observations fresh; liquidity $718,000 > $500,000 | **AUTHORIZE** | ALL_REQUIREMENTS_SATISFIED | `rcpt_773649ff0cd3f050` | MATCH | yes |
| `threshold-fail-v1` | available_liquidity observed at $92,440 (< $500,000) | **DENY** | THRESHOLD_NOT_MET | `rcpt_2747dfa85b7d6a0a` | MATCH | yes |
| `missing-v1` | Demo Lab `remove` on req_price: the price observation is dropped from the evidence set | **ABSTAIN** | MISSING_REQUIRED_EVIDENCE | `rcpt_ff12f962cad8833e` | MATCH | yes |
| `stale-v1` | Demo Lab `stale` on req_price: the observation is re-stamped 120 s old (max age 30 s) | **REFRESH** | EVIDENCE_STALE | `rcpt_8df4fe5e5fc1c5e0` | MATCH | yes |
| `conflict-v1` | Demo Lab `conflict` on req_liquidity: a second source reports $89,750 vs $718,000 (tolerance 10 %) | **ESCALATE** | CRITICAL_SOURCE_CONFLICT | `rcpt_e53da23955afdb49` | MATCH | yes |
| `grand-prize-v1` | liquidity $92,440 (DENY), then the simulated market recovers to $718,000 and evidence is re-acquired | **AUTHORIZE** | ALL_REQUIREMENTS_SATISFIED | `rcpt_7d76473940e0c6f0` | MATCH | yes |

State mapping (Document 02 §33): VALID → AUTHORIZE · THRESHOLD FAIL → DENY · MISSING → ABSTAIN · STALE → REFRESH · CONFLICT → ESCALATE.

Injected observations are attributed to the provider `demo-lab` and badged `INJECTED`; they never reuse a real provider's name, and they are evaluated by the same engine as acquired evidence.
