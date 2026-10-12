# 07 — Demo scenarios

Generated 2026-10-12T07:10:12.513000+00:00 · commit `76a872cf144d4a3528736b8060bec6384bc60a2f` · each run goes through the HTTP API (`POST /api/v1/demo/scenarios/{id}/run`)

| Scenario | Evidence mutation | Final state | Reason codes | Receipt | Replay | Passed |
|---|---|---|---|---|---|---|
| `valid-v1` | none - all three observations fresh; liquidity $718,000 > $500,000 | **AUTHORIZE** | ALL_REQUIREMENTS_SATISFIED | `rcpt_c454bdda01ac5ab3` | MATCH | yes |
| `threshold-fail-v1` | available_liquidity observed at $92,440 (< $500,000) | **DENY** | THRESHOLD_NOT_MET | `rcpt_fafb4b351dbceb59` | MATCH | yes |
| `missing-v1` | Demo Lab `remove` on req_price: the price observation is dropped from the evidence set | **ABSTAIN** | MISSING_REQUIRED_EVIDENCE | `rcpt_a7ee4e6fcdf2d6ee` | MATCH | yes |
| `stale-v1` | Demo Lab `stale` on req_price: the observation is re-stamped 120 s old (max age 30 s) | **REFRESH** | EVIDENCE_STALE | `rcpt_5fe45fd3e013896d` | MATCH | yes |
| `conflict-v1` | Demo Lab `conflict` on req_liquidity: a second source reports $89,750 vs $718,000 (tolerance 10 %) | **ESCALATE** | CRITICAL_SOURCE_CONFLICT | `rcpt_2f3e41d6713e0284` | MATCH | yes |
| `grand-prize-v1` | liquidity $92,440 (DENY), then the simulated market recovers to $718,000 and evidence is re-acquired | **AUTHORIZE** | ALL_REQUIREMENTS_SATISFIED | `rcpt_dbe5ad1b583d31f3` | MATCH | yes |

State mapping (Document 02 §33): VALID → AUTHORIZE · THRESHOLD FAIL → DENY · MISSING → ABSTAIN · STALE → REFRESH · CONFLICT → ESCALATE.

Injected observations are attributed to the provider `demo-lab` and badged `INJECTED`; they never reuse a real provider's name, and they are evaluated by the same engine as acquired evidence.
