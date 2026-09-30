# Security model

## What is protected

The principal's capital held by (or routed through) the `EvidenceGate`. The protected property: **an economic action
executes only if the Evidence Contract the principal froze was satisfied by fresh, consistent, provenance-bearing
evidence, and only the exact action bound to that evidence can execute, once, before the evidence expires.**

## Layered guards

| Layer | Guard | Where |
|---|---|---|
| Compiler | Deterministic reference parse; LLM draft / user override accepted only if **at least as strict** (threshold, operator, amount, asset, currency, freshness, TTL, failure policies, implicit requirements) | `intent/preservation.py` |
| Contract validation | Failure policies can never map to AUTHORIZE; finite bounded thresholds; TTL ≤ 600 s; amount ≤ limits | `decision_engine/validator.py` |
| Evidence boundary | Adapters return raw observations or typed failures; strict numeric and ISO-8601 parsing; malformed values stay visible and become INVALID, never coerced | `evidence/normalizer.py`, `domain/models.py` |
| Payments | Network / asset / payTo / per-request cap / budget checked **before** signing; one EIP-3009 signature per acquisition; resend of the identical payload only | `payments/x402/client.py` |
| Engine | Pure function; provenance, unit, type, range, precision, timestamp, collision, dedupe, permitted-source and cost validation; fixed precedence; no score | `decision_engine/engine.py` |
| Authorization | Only from AUTHORIZE; action bounded by contract (type, asset, amount, raw spend, no native value, deadline ≤ evidence expiry, nonce bound to the decision fingerprint); calldata re-decoded (`verify_spend`) | `decision_engine/authorization.py`, `execution/adapters.py` |
| Backend double guard | Snapshot must replay to the same fingerprint; chain time must be before expiry; onchain state read before execution (exists, unconsumed, unexpired, executor, action hash) | `services/workflow.py` |
| **Contract** | Role-gated authorize; intent ↔ contract hash binding; recomputed authorization id; TTL ≤ 600 s; exact action hash (chain id + gate address included); executor-only; deadline; exact `msg.value`; allow-listed targets; exact approval reset to 0; output floor; single use; pausable; reentrancy guard | `contracts/src/EvidenceGate.sol` |
| Receipts & audit | Receipt hash committed onchain; hash-chained audit log; replay recomputes everything from immutable snapshots | `services/receipts.py`, `services/audit.py` |

The backend is convenience. A judge or attacker calling `executeAuthorized` directly is stopped by the contract
(tested: wrong amount → `ActionHashMismatch`, wrong key → `UnauthorizedExecutor`, unknown id →
`AuthorizationNotFound`, reuse → `AuthorizationAlreadyConsumed`, late → `AuthorizationExpired`).

## Fail-closed behaviour (INV-12)

| Uncertainty | Outcome |
|---|---|
| Source unavailable / rate-limited / malformed / unconfigured | ABSTAIN (`MISSING_REQUIRED_EVIDENCE` with the cause) |
| Payment fails or budget would be exceeded | ABSTAIN (`EVIDENCE_BUDGET_EXHAUSTED`); unknown or negative spend never offsets real spend |
| Evidence too old, or authority window < 1 s | REFRESH |
| Valid sources disagree beyond tolerance | ESCALATE (no source silently chosen, no majority vote) |
| NaN, Infinity, overflow-sized, over-precise, wrong unit/type, future or ambiguous timestamp, missing hashes, colliding ids | item INVALID → ABSTAIN |
| RPC down | `503 CHAIN_UNAVAILABLE` / `ChainUnavailable`; never a fake confirmation |
| Transaction pending | `504 CHAIN_TX_PENDING`; reconciled by hash, never blindly resubmitted |
| Misconfigured Monad mode | startup error; never a silent local fallback |

## Findings fixed during review

The benchmark review found ten engine defects, each now pinned by a regression test
(`apps/api/tests/engine/test_engine_findings.py`): 28-digit decimal rounding in commitments (F1), case-folded strings in
canonical form (F2), crashes on pathological numbers (F3), negative costs hiding budget exhaustion (F4), raw spend not
bounded by the contract (F5), fingerprint not committing to failures / permitted sources (F6), evidence-id collisions
resolved by hash order (F7), shared-upstream independence (F8), zero-length authority windows (F9), explanations
rounding a violation onto its boundary (F10).

## Keys

| Key | Role | Exposure |
|---|---|---|
| Authorizer | `AUTHORIZER_ROLE` (registerIntent, authorize, recordReceipt) | server env only |
| Executor | the only caller of `executeAuthorized` | server env only |
| Pauser | `PAUSER_ROLE` | server env only; separate from the authorizer |
| Evidence payer | signs x402 EIP-3009 authorizations for evidence | server env only; never the executor |
| Deployer / admin | `DEFAULT_ADMIN_ROLE` (targets, sweep) | deploy time only |

`LOCAL_EVM` uses well-known development keys that are never funded on a public network. `/api/v1/system` never
returns secrets. API hardening: optional `X-API-Key` for mutating requests, per-IP rate limit, request body cap,
`nosniff` / `DENY` / `no-referrer` / `no-store` headers, request ids, JSON access logs.

See [evidence/08-known-limitations.md](evidence/08-known-limitations.md) for what SUFFICIA does not claim.
