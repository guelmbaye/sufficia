# 07 — Demo scenarios

Generated 2026-10-07T06:23:47.339849+00:00 · commit `16d89fa5ce9a73e9b0ea5315fb4859ad89d0d343` · each run goes through the HTTP API (`POST /api/v1/demo/scenarios/{id}/run`)

| Scenario | Evidence mutation | Final state | Reason codes | Receipt | Replay | Passed |
|---|---|---|---|---|---|---|
| `valid-v1` | none - all three observations fresh; liquidity $718,000 > $500,000 | **AUTHORIZE** | ALL_REQUIREMENTS_SATISFIED | `rcpt_9ed15f41a989962e` | MATCH | yes |
| `threshold-fail-v1` | available_liquidity observed at $92,440 (< $500,000) | **DENY** | THRESHOLD_NOT_MET | `rcpt_e4c26646429cbbf9` | MATCH | yes |
| `missing-v1` | Demo Lab `remove` on req_price: the price observation is dropped from the evidence set | **ABSTAIN** | MISSING_REQUIRED_EVIDENCE | `rcpt_f869ca5af9b375bf` | MATCH | yes |
| `stale-v1` | Demo Lab `stale` on req_price: the observation is re-stamped 120 s old (max age 30 s) | **REFRESH** | EVIDENCE_STALE | `rcpt_e90da85c6c3f7d06` | MATCH | yes |
| `conflict-v1` | Demo Lab `conflict` on req_liquidity: a second source reports $89,750 vs $718,000 (tolerance 10 %) | **ESCALATE** | CRITICAL_SOURCE_CONFLICT | `rcpt_7a47103cade99409` | MATCH | yes |
| `grand-prize-v1` | liquidity $92,440 (DENY), then the simulated market recovers to $718,000 and evidence is re-acquired | **AUTHORIZE** | ALL_REQUIREMENTS_SATISFIED | `rcpt_973d6b393597b776` | MATCH | yes |

State mapping (Document 02 §33): VALID → AUTHORIZE · THRESHOLD FAIL → DENY · MISSING → ABSTAIN · STALE → REFRESH · CONFLICT → ESCALATE.

Injected observations are attributed to the provider `demo-lab` and badged `INJECTED`; they never reuse a real provider's name, and they are evaluated by the same engine as acquired evidence.
