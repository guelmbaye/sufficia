# 05 — x402 proof

Generated 2026-10-07T06:23:47.339562+00:00 · commit `16d89fa5ce9a73e9b0ea5315fb4859ad89d0d343`

> **SIMULATED SETTLEMENT.** These payments were made against the *local x402 reference resource server* (`/_dev/x402`): the client performed the full x402 v2 `exact` flow (402 → EIP-3009 TransferWithAuthorization signed with EIP-712 → `PAYMENT-SIGNATURE` → verified server-side, nonce single-use), but nothing was settled onchain. Set `X402_EVIDENCE_URL` + `EVIDENCE_PAYMENT_PRIVATE_KEY` and `EVIDENCE_PROFILE=live` for a Monad-settled payment (facilitator `https://x402-facilitator.molandak.org`).

- Network: `eip155:10143` · asset: `0x534b2f3A21130d7a60830c2Df862319e593943A3` (USDC, EIP-712 domain `USDC` v2) · scheme: `exact`

| Requirement | Provider | Quote | Paid | Status | Payment reference | Acquisition | Evidence item | Response hash |
|---|---|---|---|---|---|---|---|---|
| req_liquidity | x402-reference | 0.02 USDC | 0.02 signed, not settled onchain | SIMULATED | `0x02a0aee8427c414274ee1d4035caa0fea2fc260055165f66ca56b62ec7c762df` | `acq_3dbe2faae325c65a` | `ev_12751978640011b4` | `0xf3a9ff26b64e18fd6db27bbf0f929f48c7a1a3e181a64fcff1b5175b494624ff` |

Payment success never makes evidence valid: the paid observation above went through the same normalizer and Decision Engine as every other item (see 02-security-invariants.md, INV-12).
