# ADR 0004 — Authority windows bounded by evidence, in whole seconds

**Status:** accepted

**Context.** Authority must not outlive its evidence (INV-09), and `EvidenceGate` stores `issuedAt`/`expiresAt` as
whole seconds, requiring `expiresAt > issuedAt` and `expiresAt > block.timestamp`.

**Decision.** `expires_at = min(evaluated_at + contract TTL, earliest mandatory evidence expiry)`. If less than one
whole second of authority remains, the decision is `REFRESH` with `AUTHORITY_WINDOW_EXHAUSTED` instead of an
AUTHORIZE that could never be committed. Before authorizing and before executing, the backend compares against chain
time; the contract re-checks.

**Consequences.** Evidence observed exactly at its maximum age is *fresh* but cannot create authority (finding F9);
executions happen within seconds of fresh evidence; an expired authorization produces an EXPIRED receipt and no
transaction.
