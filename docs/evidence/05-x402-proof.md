# 05 — x402 proof

Generated 2026-09-30T21:18:28.447677+00:00 · commit `7af4b7712a69ee13816ca1dc2f2fb8c7ead2c4bf`

> **SIMULATED SETTLEMENT.** These payments were made against the *local x402 reference resource server* (`/_dev/x402`): the client performed the full x402 v2 `exact` flow (402 → EIP-3009 TransferWithAuthorization signed with EIP-712 → `PAYMENT-SIGNATURE` → verified server-side, nonce single-use), but nothing was settled onchain. Set `X402_EVIDENCE_URL` + `EVIDENCE_PAYMENT_PRIVATE_KEY` and `EVIDENCE_PROFILE=live` for a Monad-settled payment (facilitator `https://x402-facilitator.molandak.org`).

- Network: `eip155:10143` · asset: `0x534b2f3A21130d7a60830c2Df862319e593943A3` (USDC, EIP-712 domain `USDC` v2) · scheme: `exact`

| Requirement | Provider | Quote | Paid | Status | Payment reference | Acquisition | Evidence item | Response hash |
|---|---|---|---|---|---|---|---|---|
| req_liquidity | x402-reference | 0.02 USDC | 0.02 signed, not settled onchain | SIMULATED | `0xbda615a93036afd52c2153c20e46ef8d12d59220b4f7955a467da2ad7b78f1ce` | `acq_52f199e618fe59bf` | `ev_4aef8634c364bd3a` | `0x522e56380048a4f90da22641f51a32f23cd1d13ad01bc4a64def0603f0068614` |

Payment success never makes evidence valid: the paid observation above went through the same normalizer and Decision Engine as every other item (see 02-security-invariants.md, INV-12).
