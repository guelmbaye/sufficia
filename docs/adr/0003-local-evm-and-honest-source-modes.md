# ADR 0003 — Real bytecode locally, and source modes that are never blurred

**Status:** accepted

**Context.** Judges must be able to run the whole system without keys or network, but the product claim is about
Monad execution and real evidence markets.

**Decision.** `CHAIN_MODE=LOCAL_EVM` deploys the compiled `EvidenceGate` on an in-process py-evm chain and uses the same
gateway code (local signing, receipts, revert decoding) as Monad modes; it is labelled "not Monad" everywhere,
including the proof file. Evidence carries a mode — `LIVE`, `RECORDED`, `SIMULATED`, `INJECTED` — shown as a badge.
The local x402 reference server verifies real EIP-712 signatures but reports settlement `SIMULATED`. Demo Lab
injections are attributed to the provider `demo-lab`, never to a real provider.

**Consequences.** Every run is reproducible offline; Monad deployment is configuration; nothing simulated can be
mistaken for a live observation or an onchain payment.
