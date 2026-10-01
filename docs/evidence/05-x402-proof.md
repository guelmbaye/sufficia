# 05 — x402 proof

Generated 2026-10-01T18:14:10.755600+00:00 · commit `a36d59483e79c4abb30064f096d58f749d06a21a`

> **SIMULATED SETTLEMENT.** These payments were made against the *local x402 reference resource server* (`/_dev/x402`): the client performed the full x402 v2 `exact` flow (402 → EIP-3009 TransferWithAuthorization signed with EIP-712 → `PAYMENT-SIGNATURE` → verified server-side, nonce single-use), but nothing was settled onchain. Set `X402_EVIDENCE_URL` + `EVIDENCE_PAYMENT_PRIVATE_KEY` and `EVIDENCE_PROFILE=live` for a Monad-settled payment (facilitator `https://x402-facilitator.molandak.org`).

- Network: `eip155:10143` · asset: `0x534b2f3A21130d7a60830c2Df862319e593943A3` (USDC, EIP-712 domain `USDC` v2) · scheme: `exact`

| Requirement | Provider | Quote | Paid | Status | Payment reference | Acquisition | Evidence item | Response hash |
|---|---|---|---|---|---|---|---|---|
| req_liquidity | x402-reference | 0.02 USDC | 0.02 signed, not settled onchain | SIMULATED | `0x11253069c81bfc30ec5492a72943e9c21f68d07508afe3c2a81a108eca083f3f` | `acq_9482f1cede7069e6` | `ev_2ef1ce386f6f7b46` | `0x1bd373447a7cb1d4ca2fede4777a8ab871382a88221fbfc1722bc47c08a61bfd` |

Payment success never makes evidence valid: the paid observation above went through the same normalizer and Decision Engine as every other item (see 02-security-invariants.md, INV-12).
