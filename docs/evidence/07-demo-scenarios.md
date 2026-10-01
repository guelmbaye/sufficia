# 07 — Demo scenarios

Generated 2026-10-01T17:35:10.389453+00:00 · commit `a445e5face24d16aca745fbd15f0f6f1cbd9ce5f` · each run goes through the HTTP API (`POST /api/v1/demo/scenarios/{id}/run`)

| Scenario | Evidence mutation | Final state | Reason codes | Receipt | Replay | Passed |
|---|---|---|---|---|---|---|
| `valid-v1` | none - all three observations fresh; liquidity $718,000 > $500,000 | **AUTHORIZE** | ALL_REQUIREMENTS_SATISFIED | `rcpt_08a97560e03701f8` | MATCH | yes |
| `threshold-fail-v1` | available_liquidity observed at $92,440 (< $500,000) | **DENY** | THRESHOLD_NOT_MET | `rcpt_980306b8e88b44e4` | MATCH | yes |
| `missing-v1` | Demo Lab `remove` on req_price: the price observation is dropped from the evidence set | **ABSTAIN** | MISSING_REQUIRED_EVIDENCE | `rcpt_e3fd8b4aaacbbd49` | MATCH | yes |
| `stale-v1` | Demo Lab `stale` on req_price: the observation is re-stamped 120 s old (max age 30 s) | **REFRESH** | EVIDENCE_STALE | `rcpt_880f3f3d03080f33` | MATCH | yes |
| `conflict-v1` | Demo Lab `conflict` on req_liquidity: a second source reports $89,750 vs $718,000 (tolerance 10 %) | **ESCALATE** | CRITICAL_SOURCE_CONFLICT | `rcpt_c00fef28ff904127` | MATCH | yes |
| `grand-prize-v1` | liquidity $92,440 (DENY), then the simulated market recovers to $718,000 and evidence is re-acquired | **AUTHORIZE** | ALL_REQUIREMENTS_SATISFIED | `rcpt_ff3a2195fb89b62a` | MATCH | yes |

State mapping (Document 02 §33): VALID → AUTHORIZE · THRESHOLD FAIL → DENY · MISSING → ABSTAIN · STALE → REFRESH · CONFLICT → ESCALATE.

Injected observations are attributed to the provider `demo-lab` and badged `INJECTED`; they never reuse a real provider's name, and they are evaluated by the same engine as acquired evidence.
