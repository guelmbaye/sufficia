# 05 — x402 proof

Generated 2026-09-30T22:07:30.796239+00:00 · commit `364154322be75056eed7a55374ac8c9dfc6cd274`

> **SIMULATED SETTLEMENT.** These payments were made against the *local x402 reference resource server* (`/_dev/x402`): the client performed the full x402 v2 `exact` flow (402 → EIP-3009 TransferWithAuthorization signed with EIP-712 → `PAYMENT-SIGNATURE` → verified server-side, nonce single-use), but nothing was settled onchain. Set `X402_EVIDENCE_URL` + `EVIDENCE_PAYMENT_PRIVATE_KEY` and `EVIDENCE_PROFILE=live` for a Monad-settled payment (facilitator `https://x402-facilitator.molandak.org`).

- Network: `eip155:10143` · asset: `0x534b2f3A21130d7a60830c2Df862319e593943A3` (USDC, EIP-712 domain `USDC` v2) · scheme: `exact`

| Requirement | Provider | Quote | Paid | Status | Payment reference | Acquisition | Evidence item | Response hash |
|---|---|---|---|---|---|---|---|---|
| req_liquidity | x402-reference | 0.02 USDC | 0.02 signed, not settled onchain | SIMULATED | `0xb1bb3bb87dcf49e21746b501a98b5fc400aa3e3f586f0c34c9e124d1c75e93aa` | `acq_faaa29ef34973622` | `ev_77a11988c9fcb52f` | `0xacaf4083a66ce7995e7ebd3308a00bc4a8fe6ea9bfec5d53ce9d63cf56396bef` |

Payment success never makes evidence valid: the paid observation above went through the same normalizer and Decision Engine as every other item (see 02-security-invariants.md, INV-12).
