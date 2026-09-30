# 05 — x402 proof

Generated 2026-09-30T21:21:05.758214+00:00 · commit `e90fec6a0df4ea3694fbb7af6a20d81194f8f406`

> **SIMULATED SETTLEMENT.** These payments were made against the *local x402 reference resource server* (`/_dev/x402`): the client performed the full x402 v2 `exact` flow (402 → EIP-3009 TransferWithAuthorization signed with EIP-712 → `PAYMENT-SIGNATURE` → verified server-side, nonce single-use), but nothing was settled onchain. Set `X402_EVIDENCE_URL` + `EVIDENCE_PAYMENT_PRIVATE_KEY` and `EVIDENCE_PROFILE=live` for a Monad-settled payment (facilitator `https://x402-facilitator.molandak.org`).

- Network: `eip155:10143` · asset: `0x534b2f3A21130d7a60830c2Df862319e593943A3` (USDC, EIP-712 domain `USDC` v2) · scheme: `exact`

| Requirement | Provider | Quote | Paid | Status | Payment reference | Acquisition | Evidence item | Response hash |
|---|---|---|---|---|---|---|---|---|
| req_liquidity | x402-reference | 0.02 USDC | 0.02 signed, not settled onchain | SIMULATED | `0x664b53d49e48451e8c0d531940a497945e8b71a612621875d0e29679d551ed25` | `acq_7ce91fd8b5ff7cac` | `ev_825898c055b2c96e` | `0xc29bd291c712407798dcbadb30d1691a21e07b8b38c08731ec4cff84e35c12ab` |

Payment success never makes evidence valid: the paid observation above went through the same normalizer and Decision Engine as every other item (see 02-security-invariants.md, INV-12).
