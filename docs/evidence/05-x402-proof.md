# 05 — x402 proof

Generated 2026-10-12T07:10:12.511002+00:00 · commit `76a872cf144d4a3528736b8060bec6384bc60a2f`

> **SIMULATED SETTLEMENT.** These payments were made against the *local x402 reference resource server* (`/_dev/x402`): the client performed the full x402 v2 `exact` flow (402 → EIP-3009 TransferWithAuthorization signed with EIP-712 → `PAYMENT-SIGNATURE` → verified server-side, nonce single-use), but nothing was settled onchain. Set `X402_EVIDENCE_URL` + `EVIDENCE_PAYMENT_PRIVATE_KEY` and `EVIDENCE_PROFILE=live` for a Monad-settled payment (facilitator `https://x402-facilitator.molandak.org`).

- Network: `eip155:10143` · asset: `0x534b2f3A21130d7a60830c2Df862319e593943A3` (USDC, EIP-712 domain `USDC` v2) · scheme: `exact`

| Requirement | Provider | Quote | Paid | Status | Payment reference | Acquisition | Evidence item | Response hash |
|---|---|---|---|---|---|---|---|---|

Payment success never makes evidence valid: the paid observation above went through the same normalizer and Decision Engine as every other item (see 02-security-invariants.md, INV-12).
