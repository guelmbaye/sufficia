# 07 — Demo scenarios

Generated 2026-09-30T21:18:28.447735+00:00 · commit `7af4b7712a69ee13816ca1dc2f2fb8c7ead2c4bf` · each run goes through the HTTP API (`POST /api/v1/demo/scenarios/{id}/run`)

| Scenario | Evidence mutation | Final state | Reason codes | Receipt | Replay | Passed |
|---|---|---|---|---|---|---|
| `valid-v1` | none - all three observations fresh; liquidity $718,000 > $500,000 | **AUTHORIZE** | ALL_REQUIREMENTS_SATISFIED | `rcpt_a94f1c44a568b4d3` | MATCH | yes |
| `threshold-fail-v1` | available_liquidity observed at $92,440 (< $500,000) | **DENY** | THRESHOLD_NOT_MET | `rcpt_16140f0d9c9cf98b` | MATCH | yes |
| `missing-v1` | Demo Lab `remove` on req_price: the price observation is dropped from the evidence set | **ABSTAIN** | MISSING_REQUIRED_EVIDENCE | `rcpt_1973bd069a555f51` | MATCH | yes |
| `stale-v1` | Demo Lab `stale` on req_price: the observation is re-stamped 120 s old (max age 30 s) | **REFRESH** | EVIDENCE_STALE | `rcpt_8580bfb91b8fd721` | MATCH | yes |
| `conflict-v1` | Demo Lab `conflict` on req_liquidity: a second source reports $89,750 vs $718,000 (tolerance 10 %) | **ESCALATE** | CRITICAL_SOURCE_CONFLICT | `rcpt_8bf16b9c301f2729` | MATCH | yes |
| `grand-prize-v1` | liquidity $92,440 (DENY), then the simulated market recovers to $718,000 and evidence is re-acquired | **AUTHORIZE** | ALL_REQUIREMENTS_SATISFIED | `rcpt_d0e06a44c1ef13d2` | MATCH | yes |

State mapping (Document 02 §33): VALID → AUTHORIZE · THRESHOLD FAIL → DENY · MISSING → ABSTAIN · STALE → REFRESH · CONFLICT → ESCALATE.

Injected observations are attributed to the provider `demo-lab` and badged `INJECTED`; they never reuse a real provider's name, and they are evaluated by the same engine as acquired evidence.
