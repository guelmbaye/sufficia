# 04 — Monad proof

> **LOCAL REHEARSAL — NOT MONAD.** This file was generated with `CHAIN_MODE=LOCAL_EVM`: the real EvidenceGate bytecode ran on an in-process py-evm chain. Regenerate on Monad testnet with `CHAIN_MODE=MONAD_TESTNET make evidence` after `make deploy-testnet` (see docs/deployment.md).

Generated 2026-10-01T17:35:10.388810+00:00 · commit `a445e5face24d16aca745fbd15f0f6f1cbd9ce5f`

## Network

- Mode: `LOCAL_EVM` · network: local-evm (py-evm, not Monad) · chain id: `131277322940537`
- EvidenceGate: `0x6E05f58eEddA592f34DD9105b1827f252c509De0`
- Authorizer: `0x1563915e194D8CfBA1943570603F7606A3115508` · Executor: `0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB`
- Deployment: `{"deploy_usdc": "0xd62b2ff60630b97f482dfc70135fba8c9a77c971306e3940737676138fa1432f", "deploy_token_x": "0xf1044435c119a3bf7a1ffd376ddfed681dfb4793164705e7dc1b17539e8caf55", "deploy_router": "0xa7a100784d4ebc575e1e5a2af76188811cfad016b90125a3333d25303eb2ae11", "set_rate": "0x416ae6a927744a204f851618efbb2ed724971ad0bd25ecbd80140a6ffb9fb685", "mint_router_reserve": "0x35551e25f0b734dabdfe67fa497a94494d76294eaa01d8278b1379b1259ccfd1", "deploy_gate": "0xc66b49d7af530bb8c1f74374fc19d1aa2450319e5501be6a488a30942b9eb84e", "fund_gate": "0x955f0c7ac912731b8121cee299239fb6492c07e81dfacb735deee0125f8048e6"}`

## Successful protected execution (grand-prize-v1)

- Evidence Contract hash: `0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e`
- Evidence root: `0x14695bc94b90dc38925c266cd8a9556ae5a150a60be156fee52f29e637dc82bc`
- Decision: `AUTHORIZE` (`dec_a2896a99c2e652cf`)
- Action hash: `0xb8c6d2852b45640c5b74e2570bd20f5b75bcde517b7f86a160ef89114704bf59`
- Authorization id: `0xf6a39d922de6bb2b5671c972539d339492dfde7559c102b831748867ed121084` (expires 2026-10-01T17:35:38.905507Z)
- Intent registration tx: `0x3470b45baa51a1cf70b60e442469cffa561ab897bbe8ceca70950ffb7b159f43`
- Authorization tx: `0x5493526ebb90b07feb92daefecf10c5fcb23aef3a1688e80506398bb88865bb0` (block 14)
- Execution tx: `0xdfbe4328e887e9a9c33376dfc1d49f7b650d2ec2d1e5fed643d0b328a21c50eb` (status EXECUTED)
- Receipt hash: `0x28d67d9d17fb31d14bc472951bc9c9b6ea57059afd7b5475f86a946cac627ff7` · commitment tx: `0x1572b1f7239c031a29805227333dafca2255edc4681a807348ae376cd8694415` (CONFIRMED)

## EvidenceGate events (decoded from receipts)

- INTENT_REGISTER `IntentRegistered` {"contractHash": "0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e", "intentId": "0xd3d78ddbf92604deeed3583efe31b08375f93066e6c790445c9b8a66851a2198", "registeredAt": 1790876108}
- AUTHORIZATION `ExecutionAuthorized` {"actionHash": "0xb8c6d2852b45640c5b74e2570bd20f5b75bcde517b7f86a160ef89114704bf59", "authorizationId": "0xf6a39d922de6bb2b5671c972539d339492dfde7559c102b831748867ed121084", "contractHash": "0x7ed7409fed71e5a564460609d2823bfd7b0201e59af3c53c63e0b8068116838e", "evidenceRoot": "0x14695bc94b90dc38925c266cd8a9556ae5a150a60be156fee52f29e637dc82bc", "executor": "0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB", "expiresAt": 1790876138, "intentId": "0xd3d78ddbf92604deeed3583efe31b08375f93066e6c790445c9b8a66851a2198"}
- PROTECTED_EXECUTION `AuthorizationConsumed` {"actionHash": "0xb8c6d2852b45640c5b74e2570bd20f5b75bcde517b7f86a160ef89114704bf59", "authorizationId": "0xf6a39d922de6bb2b5671c972539d339492dfde7559c102b831748867ed121084", "executor": "0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB"}
- PROTECTED_EXECUTION `ProtectedExecution` {"actionHash": "0xb8c6d2852b45640c5b74e2570bd20f5b75bcde517b7f86a160ef89114704bf59", "authorizationId": "0xf6a39d922de6bb2b5671c972539d339492dfde7559c102b831748867ed121084", "resultHash": "0xc17bc06792c2ffb20ca1334de224715d29c84cec91ac45e70473e3964c7e3628", "target": "0x7d73424a8256C0b2BA245e5d5a3De8820E45F390"}
- RECEIPT_COMMITMENT `ReceiptCommitted` {"authorizationId": "0xf6a39d922de6bb2b5671c972539d339492dfde7559c102b831748867ed121084", "receiptHash": "0x28d67d9d17fb31d14bc472951bc9c9b6ea57059afd7b5475f86a946cac627ff7"}

## Blocked decision (no transaction)

- Decision `dec_93f095c95b07bdcd`: **DENY** ['THRESHOLD_NOT_MET'] — Available liquidity is $92,440. The Evidence Contract requires more than $500,000. No execution authorization was issued. 100 USDC did not move.
- Receipt `rcpt_b2b88f26e2a36745`: execution status `BLOCKED`, tx_hash `None`

## Replay

- Result **MATCH**, chain verification `VERIFIED`, 24/24 checks passing, market queried: False
