# API reference

Base URL `http://127.0.0.1:8000`. Interactive docs at `/docs`; machine-readable spec in
[`packages/schemas/openapi.json`](../packages/schemas/openapi.json). The web app reaches the same routes through its
same-origin proxy (`http://127.0.0.1:3000/api/v1/*`).

**Conventions**
- Authority-carrying numbers are exact: Decimals are JSON strings (`"0.428"`, `"500000"`); integers above 2^53
  (raw token amounts) are decimal strings. Timestamps are UTC ISO-8601 with microseconds and `Z`.
- Errors: `{"error": {"code": "...", "message": "...", "detail": {...}}}` with a meaningful HTTP status.
- Mutating requests need `X-API-Key` when `API_KEY` is set; every response carries `X-Request-ID`.

## Flow

```bash
B=http://127.0.0.1:8000/api/v1
IID=$(curl -s -XPOST $B/intents -H 'content-type: application/json' \
  -d '{"text":"Buy up to 100 USDC of TOKEN-X only if smart-money inflows exceed $1M and liquidity exceeds $500K."}' | jq -r .intent.intent_id)
CID=$(curl -s -XPOST $B/intents/$IID/compile | jq -r .contract.contract_id)
curl -s -XPOST $B/contracts/$CID/acquire | jq '.items[] | {req: .item.requirement_id, value: .item.claim.value, badge}'
DID=$(curl -s -XPOST $B/contracts/$CID/decide | jq -r .decision.decision_id)   # DENY (liquidity $92,440)
curl -s -XPOST $B/decisions/$DID/execute                                       # 403 EVIDENCE_AUTHORIZATION_REQUIRED
curl -s -XPUT $B/demo/market -H 'content-type: application/json' -d '{"values":{"available_liquidity":"718000"}}'
curl -s -XPOST $B/contracts/$CID/acquire >/dev/null
DID=$(curl -s -XPOST $B/contracts/$CID/decide | jq -r .decision.decision_id)   # AUTHORIZE
RID=$(curl -s -XPOST $B/decisions/$DID/execute | jq -r .receipt_id)            # authorize + executeAuthorized
curl -s $B/receipts/$RID | jq .audit_chain
curl -s -XPOST $B/receipts/$RID/replay | jq '{result, chain_verification}'   # MATCH, VERIFIED
```

## Endpoints

### Intents & contracts
| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/intents` | `{text, network?, protected_amount?, evidence_budget?}` → `201 {intent, intent_hash}` |
| GET | `/api/v1/intents` · `/api/v1/intents/{id}` | |
| POST | `/api/v1/intents/preview` | `{text, mode?}` — compiles without persisting (the "What must be true?" preview) |
| POST | `/api/v1/intents/{id}/compile` | `{mode?: deterministic\|llm\|auto, override?: EvidenceContract}` → frozen contract, `summary`, `preservation`. `409 CONTRACT_FROZEN` if already compiled, `422 INTENT_*` if not compilable, `422 INTENT_RELAXATION_REJECTED` for a weaker override |
| GET | `/api/v1/contracts/{id}` | contract, hash, summary, preservation report, evidence sets, decisions |

### Evidence
| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/contracts/{id}/acquire` | `{sources_per_requirement?: 1..3}` → new immutable evidence set: items (with `badge`), failures, x402 payments, timeline, spend |
| GET | `/api/v1/contracts/{id}/evidence` | current (latest) evidence set |
| GET | `/api/v1/evidence-sets/{id}` | any evidence set |

### Decisions, authorization, execution
| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/contracts/{id}/verify` | dry run of the engine (no decision stored, no authority) |
| POST | `/api/v1/contracts/{id}/decide` | `{evidence_set_id?}` → decision, `authority` window, `receipts` (a BLOCKED receipt is created for every non-AUTHORIZE decision). `409 EVIDENCE_NOT_ACQUIRED` |
| GET | `/api/v1/decisions/{id}` | decision + authorization + executions + receipts |
| POST | `/api/v1/decisions/{id}/authorize` | double guard (Document 04 §64) then `registerIntent` + `authorize` onchain. `403 EVIDENCE_AUTHORIZATION_REQUIRED` unless AUTHORIZE; `409 DECISION_INTEGRITY`, `409 ACTION_VERIFICATION_FAILED`, `403 AUTHORITY_EXPANSION`, `409 AUTHORIZATION_EXPIRED`, `502 CHAIN_TX_FAILED`, `504 CHAIN_TX_PENDING`. Idempotent. |
| POST | `/api/v1/decisions/{id}/execute` | authorize (if needed) + execute. **403 for every non-AUTHORIZE decision.** |
| GET | `/api/v1/authorizations/{id}` | stored state + live onchain state + transactions |
| POST | `/api/v1/authorizations/{id}/execute` | reads onchain state first (exists, unconsumed, unexpired, executor, action hash) then `executeAuthorized`. The result lists the protected-execution and receipt-commitment transactions. `409 AUTHORIZATION_EXPIRED` (+ EXPIRED receipt, id in `detail.receipt_id`), `409 ACTION_HASH_MISMATCH`, `409 AUTHORIZATION_ALREADY_CONSUMED`, `409 EXECUTION_REVERTED` (+ FAILED receipt). Idempotent per authorization. |

### Receipts, replay, audit
| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/receipts` · `/api/v1/receipts/{id}` · `/api/v1/decisions/{id}/receipt` | receipt body, `receipt_hash`, onchain `commitment`, `audit_chain`, `timeline` (intent → contract → evidence → decision → blocked \| authorized → committed → executed \| failed \| expired → receipt, from stored timestamps and tx hashes) and `evidence_reasons` (engine reason code per requirement, e.g. `EVIDENCE_STALE`; display data outside the hashed body) |
| POST | `/api/v1/receipts/{id}/replay` | `MATCH`/`MISMATCH` with every check (receipt integrity, decision replay incl. engine version and explanation, authority binding, onchain state); `original` / `replay` carry state, reason codes and the first reason |
| POST | `/api/v1/receipts/{id}/tamper-test` | changes one numeric evidence value by its smallest unit **in memory only** and reports the recomputed `evidence_root` (`MISMATCH`) and receipt hash (`receipt_integrity: INVALID`); `stored_data_modified: false` |
| GET | `/api/v1/audit` · `/api/v1/audit/verify` | hash-chained audit events and integrity check |

### System & chain
| Method | Path | Notes |
|---|---|---|
| GET | `/health` · `/health/ready` | liveness / readiness (database, chain) |
| GET | `/api/v1/system` | versions, evidence profile, x402 config, chain info (never secrets) |
| GET | `/api/v1/chain` · `/api/v1/chain/transactions` · `/api/v1/chain/artifacts/{EvidenceGate\|DemoSwapRouter\|DemoERC20}` | chain info + health, persisted transactions, ABIs |
| GET | `/api/v1/stats` · `/api/v1/benchmark` · POST `/api/v1/benchmark/run` | counters, latest benchmark report, in-process benchmark run |

### Demo Lab (only when `DEMO_MODE=true`, otherwise 404)
| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/demo/evidence/{evidence_id}/{valid\|stale\|remove\|conflict\|threshold}` | changes exactly one item → new evidence set (items badged `INJECTED`, provider `demo-lab`) |
| POST | `/api/v1/demo/contracts/{id}/conditions/{condition}?requirement_id=` | same, targeting a requirement |
| POST | `/api/v1/demo/contracts/{id}/test/{valid\|threshold\|missing\|stale\|conflict}` | the web app's *Test the gate*: sets a passing market baseline, applies exactly one condition, acquires and decides → `201 {condition, requirement_id, expected_state, evidence, decision}`. Order-independent: each call starts from the same baseline. Expected: AUTHORIZE · DENY · ABSTAIN · REFRESH · ESCALATE |
| GET / PUT | `/api/v1/demo/market` · POST `/api/v1/demo/market/reset` | simulated market values used by `SIMULATED` sources |
| GET | `/api/v1/demo/scenarios` · `/api/v1/demo/scenarios/{id}` | versioned scenarios |
| POST | `/api/v1/demo/scenarios/{id}/run` (also `/demo/scenarios/{id}/run`) | `{execute?: bool, stop_before?: op}` → full timeline |
| * | `/_dev/x402/*` | local x402 reference resource server (402 → verified EIP-3009 signature → evidence; settlement `SIMULATED`) |
