# 04 — Monad proof

> **LOCAL REHEARSAL — NOT MONAD.** This file was generated with `CHAIN_MODE=LOCAL_EVM`: the real EvidenceGate bytecode ran on an in-process py-evm chain. Regenerate on Monad testnet with `CHAIN_MODE=MONAD_TESTNET make evidence` after `make deploy-testnet` (see docs/deployment.md).

Generated 2026-10-07T06:23:47.338940+00:00 · commit `16d89fa5ce9a73e9b0ea5315fb4859ad89d0d343`

## Network

- Mode: `LOCAL_EVM` · network: local-evm (py-evm, not Monad) · chain id: `131277322940537`
- EvidenceGate: `0x6E05f58eEddA592f34DD9105b1827f252c509De0`
- Authorizer: `0x1563915e194D8CfBA1943570603F7606A3115508` · Executor: `0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB`
- Deployment transactions:
  - deploy_usdc: `0xd62b2ff60630b97f482dfc70135fba8c9a77c971306e3940737676138fa1432f`
  - deploy_token_x: `0xf1044435c119a3bf7a1ffd376ddfed681dfb4793164705e7dc1b17539e8caf55`
  - deploy_router: `0xa7a100784d4ebc575e1e5a2af76188811cfad016b90125a3333d25303eb2ae11`
  - set_rate: `0x416ae6a927744a204f851618efbb2ed724971ad0bd25ecbd80140a6ffb9fb685`
  - mint_router_reserve: `0x35551e25f0b734dabdfe67fa497a94494d76294eaa01d8278b1379b1259ccfd1`
  - deploy_gate: `0xc66b49d7af530bb8c1f74374fc19d1aa2450319e5501be6a488a30942b9eb84e`
  - fund_gate: `0x955f0c7ac912731b8121cee299239fb6492c07e81dfacb735deee0125f8048e6`

## Successful protected execution (grand-prize-v1)

- Evidence Contract hash: `0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e`
- Evidence root: `0x998e7dd2249c2150274b033ac629f5af202af71408231387ab740e7ff4052aab`
- Decision: `AUTHORIZE` (`dec_555960276576fb6f`)
- Action hash: `0x52d3300fec3184c604593fc8cdf04ca143909520fc3b288b93165f854460d770`
- Authorization id: `0x6251d310b855ad8db5f347b21dbd7cfd9c5209330c972ddf7d6fd4fa16886a0c` (expires 2026-10-07T06:24:15.587115Z)
- Intent registration tx: `0x8b7489d7e64684a9bfefe77f5b501a72ecef6cb3f4a2190b83b774071b773160`
- Authorization tx: `0xeab3d9d81022289aff7c137cebe2dedd0eb58af0044b1381204bba0622924c95` (block 14)
- Execution tx: `0x74387e0b600ed790bb5b366f0974b1201aad899db24cec05e6767d2e23c8b70f` (status EXECUTED)
- Receipt hash: `0x45bfadaf8a6899fd6e19c7bd01492817efdd17c8f403d4d75bb31f56c7b32b2f` · commitment tx: `0x65964cce88f2f6620abb2c74e9c4433ae3d2137a6105e4dabeaa7c301ca9a974` (CONFIRMED)

## EvidenceGate events (decoded from receipts)

- INTENT_REGISTER `IntentRegistered` {"contractHash": "0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e", "intentId": "0x5544e718481d6e18bc8386581b68e857f7e65599d0e96ebe017c93c82d952c08", "registeredAt": 1791354225}
- AUTHORIZATION `ExecutionAuthorized` {"actionHash": "0x52d3300fec3184c604593fc8cdf04ca143909520fc3b288b93165f854460d770", "authorizationId": "0x6251d310b855ad8db5f347b21dbd7cfd9c5209330c972ddf7d6fd4fa16886a0c", "contractHash": "0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e", "evidenceRoot": "0x998e7dd2249c2150274b033ac629f5af202af71408231387ab740e7ff4052aab", "executor": "0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB", "expiresAt": 1791354255, "intentId": "0x5544e718481d6e18bc8386581b68e857f7e65599d0e96ebe017c93c82d952c08"}
- PROTECTED_EXECUTION `AuthorizationConsumed` {"actionHash": "0x52d3300fec3184c604593fc8cdf04ca143909520fc3b288b93165f854460d770", "authorizationId": "0x6251d310b855ad8db5f347b21dbd7cfd9c5209330c972ddf7d6fd4fa16886a0c", "executor": "0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB"}
- PROTECTED_EXECUTION `ProtectedExecution` {"actionHash": "0x52d3300fec3184c604593fc8cdf04ca143909520fc3b288b93165f854460d770", "authorizationId": "0x6251d310b855ad8db5f347b21dbd7cfd9c5209330c972ddf7d6fd4fa16886a0c", "resultHash": "0xc17bc06792c2ffb20ca1334de224715d29c84cec91ac45e70473e3964c7e3628", "target": "0x7d73424a8256C0b2BA245e5d5a3De8820E45F390"}
- RECEIPT_COMMITMENT `ReceiptCommitted` {"authorizationId": "0x6251d310b855ad8db5f347b21dbd7cfd9c5209330c972ddf7d6fd4fa16886a0c", "receiptHash": "0x45bfadaf8a6899fd6e19c7bd01492817efdd17c8f403d4d75bb31f56c7b32b2f"}

## Blocked decision (no transaction)

- Decision `dec_78ea28d770d54281`: **DENY** ['THRESHOLD_NOT_MET'] — Available liquidity is $92,440. The Evidence Contract requires more than $500,000. No execution authorization was issued. 100 USDC did not move.
- Receipt `rcpt_6a3f132648c87786`: execution status `BLOCKED`, tx_hash `None`

## Replay

- Result **MATCH**, chain verification `VERIFIED`, 24/24 checks passing, market queried: False
