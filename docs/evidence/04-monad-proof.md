# 04 — Monad proof

> **LOCAL REHEARSAL — NOT MONAD.** This file was generated with `CHAIN_MODE=LOCAL_EVM`: the real EvidenceGate bytecode ran on an in-process py-evm chain. Regenerate on Monad testnet with `CHAIN_MODE=MONAD_TESTNET make evidence` after `make deploy-testnet` (see docs/deployment.md).

Generated 2026-09-30T21:21:05.757717+00:00 · commit `e90fec6a0df4ea3694fbb7af6a20d81194f8f406`

## Network

- Mode: `LOCAL_EVM` · network: local-evm (py-evm, not Monad) · chain id: `131277322940537`
- EvidenceGate: `0x6E05f58eEddA592f34DD9105b1827f252c509De0`
- Authorizer: `0x1563915e194D8CfBA1943570603F7606A3115508` · Executor: `0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB`
- Deployment: `{"deploy_usdc": "0xd62b2ff60630b97f482dfc70135fba8c9a77c971306e3940737676138fa1432f", "deploy_token_x": "0xf1044435c119a3bf7a1ffd376ddfed681dfb4793164705e7dc1b17539e8caf55", "deploy_router": "0xa7a100784d4ebc575e1e5a2af76188811cfad016b90125a3333d25303eb2ae11", "set_rate": "0x416ae6a927744a204f851618efbb2ed724971ad0bd25ecbd80140a6ffb9fb685", "mint_router_reserve": "0x35551e25f0b734dabdfe67fa497a94494d76294eaa01d8278b1379b1259ccfd1", "deploy_gate": "0xc66b49d7af530bb8c1f74374fc19d1aa2450319e5501be6a488a30942b9eb84e", "fund_gate": "0x955f0c7ac912731b8121cee299239fb6492c07e81dfacb735deee0125f8048e6"}`

## Successful protected execution (grand-prize-v1)

- Evidence Contract hash: `0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e`
- Evidence root: `0xb21aa4b5f611a49e7f710bfc7565e5e16a2b7fb5dcf7b84cd243e5b5d3c359d4`
- Decision: `AUTHORIZE` (`dec_98b1fea38f0a8447`)
- Action hash: `0x4716f9d32fcdf8515ab209e43ffc53105940a9966af14b530acc5f90c01625a9`
- Authorization id: `0x7a07b9ed25fbb863a62fb11c5ae30618de0ec58873e27378966d137a2f15077e` (expires 2026-09-30T21:21:34.217694Z)
- Intent registration tx: `0x1600e663f0376d50d0fb65f2c419252388147def9d5a4cce008fc91dc2a55655`
- Authorization tx: `0x6666451e094254a0eddee0248be4bf122fd2425700a3c968e1572a4dc3031a9f` (block 14)
- Execution tx: `0xe5cfe0dd000de323ed5f98cf34340ccb9cbc212a292358f67988982ed281f8b2` (status EXECUTED)
- Receipt hash: `0xd651cbd3a6ef5579c250d6f324f35b63ad5c8e4947fabc6797334ce8c71540b8` · commitment tx: `0x154309815ab4a92d79006e9d82cf6fd095c4943163500e0eed61615552913d79` (CONFIRMED)

## EvidenceGate events (decoded from receipts)

- INTENT_REGISTER `IntentRegistered` {"contractHash": "0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e", "intentId": "0xb928b97e446020f7360235411e8d6a3b13e1ba1d3781c841a91025b1d02ea0b9", "registeredAt": 1790803264}
- AUTHORIZATION `ExecutionAuthorized` {"actionHash": "0x4716f9d32fcdf8515ab209e43ffc53105940a9966af14b530acc5f90c01625a9", "authorizationId": "0x7a07b9ed25fbb863a62fb11c5ae30618de0ec58873e27378966d137a2f15077e", "contractHash": "0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e", "evidenceRoot": "0xb21aa4b5f611a49e7f710bfc7565e5e16a2b7fb5dcf7b84cd243e5b5d3c359d4", "executor": "0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB", "expiresAt": 1790803294, "intentId": "0xb928b97e446020f7360235411e8d6a3b13e1ba1d3781c841a91025b1d02ea0b9"}
- PROTECTED_EXECUTION `AuthorizationConsumed` {"actionHash": "0x4716f9d32fcdf8515ab209e43ffc53105940a9966af14b530acc5f90c01625a9", "authorizationId": "0x7a07b9ed25fbb863a62fb11c5ae30618de0ec58873e27378966d137a2f15077e", "executor": "0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB"}
- PROTECTED_EXECUTION `ProtectedExecution` {"actionHash": "0x4716f9d32fcdf8515ab209e43ffc53105940a9966af14b530acc5f90c01625a9", "authorizationId": "0x7a07b9ed25fbb863a62fb11c5ae30618de0ec58873e27378966d137a2f15077e", "resultHash": "0xc17bc06792c2ffb20ca1334de224715d29c84cec91ac45e70473e3964c7e3628", "target": "0x7d73424a8256C0b2BA245e5d5a3De8820E45F390"}
- RECEIPT_COMMITMENT `ReceiptCommitted` {"authorizationId": "0x7a07b9ed25fbb863a62fb11c5ae30618de0ec58873e27378966d137a2f15077e", "receiptHash": "0xd651cbd3a6ef5579c250d6f324f35b63ad5c8e4947fabc6797334ce8c71540b8"}

## Blocked decision (no transaction)

- Decision `dec_09528c15fa8ba61e`: **DENY** ['THRESHOLD_NOT_MET'] — Available liquidity is $92,440. The Evidence Contract requires more than $500,000. No execution authorization was issued. 100 USDC did not move.
- Receipt `rcpt_700d39ede993ff60`: execution status `BLOCKED`, tx_hash `None`

## Replay

- Result **MATCH**, chain verification `VERIFIED`, 22/22 checks passing, market queried: False
